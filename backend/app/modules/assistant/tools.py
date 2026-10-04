"""What the assistant can do, and what it can only ask for.

Two kinds of tool live here and the difference is the whole design.

**Read tools** the agent runs itself, in a loop, without anybody waiting: they
query this deployment's own data and hand back a compact result. Nothing here
writes, so a read tool can never be the thing that needed confirming.

**Ask tools** the agent cannot run at all. Calling one renders a real control
in the chat - a search box that saves a building, a file picker that uploads a
lease - and the turn stops there until the person uses it. Their `run` is
`None`, and that absence is what `graph.py` dispatches on: a tool with no
handler is by construction one only a human can complete.

Three rules shape every result below.

*Compact.* A tool result is input tokens on every subsequent turn of the
conversation, forever - so a list is capped and the overflow is reported as a
count, never truncated silently. `rules_for_building` deliberately omits the
quoted spans: they are the single largest thing here, and `rule_source` fetches
one on demand, for the one rule the assistant is actually about to quote.

*Deterministic.* Keys are sorted on the way out. The tool definitions render at
position 0 of every request, so the roster is built in name order; a set
iterated here would reorder the prefix and cost a cache hit.

*Owned.* Anything belonging to a person is read through `accounts.service`,
which filters on the token's `sub`. No handler here takes an owner id from the
model, because the model's input is ultimately the person's typing.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import Principal
from app.db.models import Address, AddressJurisdiction, Rule
from app.modules.accounts import service as accounts
from app.modules.accounts.schemas import Role

# ---------------------------------------------------------------------------
# What a handler is given.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Deps:
    """Everything a tool may read, and nothing it may read it from."""

    session: AsyncSession
    principal: Principal
    role: Role
    as_of: dt.date


Handler = Callable[[Deps, dict], Awaitable[dict]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    #: One line, shown in the chat while the tool runs, so a pause is legible.
    label: str
    description: str
    properties: dict
    required: tuple[str, ...] = ()
    #: `None` makes this an ask tool: only the person can complete it.
    run: Handler | None = None
    #: Whether the turn stops until the person has used the control. False for
    #: a card that is an offer rather than a question - a link needs no answer,
    #: so the agent is told it was shown and carries on writing its sentence
    #: instead of leaving the conversation waiting on a click.
    waits: bool = True

    @property
    def asks(self) -> bool:
        return self.run is None

    def wire(self) -> dict:
        """The tool definition as the API takes it.

        `strict` guarantees the input validates against this schema, which is
        what lets a handler index `args` without defending against a missing
        key on every line.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            "strict": True,
        }


# ---------------------------------------------------------------------------
# Compacting.
# ---------------------------------------------------------------------------

#: How much of a quoted span to carry. Long enough to be a real quotation,
#: short enough that four of them are not a page.
SPAN_CHARS = 700


def _clip(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _drop_empty(row: dict) -> dict:
    """Null fields are not worth a line each in the prefix.

    The system prompt already says an absent fact is absent - so a key that is
    missing means the same thing as one that is null, for a tenth of the
    tokens.
    """
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


def encode(result: dict) -> str:
    """A tool result, as the one string the API wants back.

    `sort_keys` is not tidiness: this string is cached as part of the prefix
    from the next turn on, and a dict iterated in insertion order would hash
    differently between two runs that found the same answer.
    """
    return json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Read tools.
# ---------------------------------------------------------------------------


#: Comma parts of a pasted address that are not a street or a city.
_NOT_A_PLACE = re.compile(
    r"^(?:usa|u\.s\.a\.|united states|[a-z]{2}\s*\d{5}(?:-\d{4})?|\d{5}(?:-\d{4})?|[a-z]{2})$",
    re.IGNORECASE,
)


def relax(query: str) -> tuple[str, str] | None:
    """Split a pasted address into the street and the city it is matched on.

    What a person pastes is "415 Mission St, San Francisco, CA 94105, United
    States", and the search matches one field at a time - so the whole string
    matches nothing, and so does any suffix of it. The house number has to go
    too: the book holds 2250 and 2280 Mission St and no 415, and a reader
    looking at an empty result cannot tell "this street is not on file" from
    "this building is not".

    Returns `None` when there is nothing to relax, so a one-word search is
    never re-run as itself. `narrower()` in components/assistant/cards.tsx is
    the same parse for the typed box, which offers the parts as something to
    click rather than searching them unasked.
    """
    parts = [p.strip() for p in query.split(",") if p.strip()]
    parts = [p for p in parts if not _NOT_A_PLACE.match(p)]
    if len(parts) < 2:
        return None
    street = re.sub(r"^\d+[a-z]?\s+", "", parts[0], flags=re.IGNORECASE).strip()
    city = parts[1]
    return (street, city) if street and city else None


async def _match_addresses(deps: Deps, terms: list[str], limit: int) -> list[Address]:
    """Buildings on file where every term appears in the street or the city.

    The imported book only: this is "find it among the buildings this account
    holds", and an address somebody typed in for themselves is not something
    anybody else should be able to search their way to.
    """
    stmt = select(Address).where(Address.imported)
    for term in terms:
        needle = f"%{term}%"
        stmt = stmt.where(
            or_(Address.street_address.ilike(needle), Address.postal_city.ilike(needle))
        )
    rows = await deps.session.execute(stmt.order_by(Address.address_id).limit(limit + 1))
    return list(rows.scalars())


async def _search_addresses(deps: Deps, args: dict) -> dict:
    query = args["query"].strip()
    limit = min(int(args.get("limit") or 8), 15)

    found = await _match_addresses(deps, [query], limit)
    searched = None
    if not found and (relaxed := relax(query)):
        # A pasted address, matched on its street and its city instead. Said
        # out loud in `searched_instead`, because the answer is about a
        # different building from the one that was asked for.
        found = await _match_addresses(deps, list(relaxed), limit)
        if found:
            searched = f"{relaxed[0]} in {relaxed[1]}"

    return _drop_empty(
        {
            "matches": [
                _drop_empty(
                    {
                        "address_id": a.address_id,
                        "street": a.street_address,
                        "city": a.postal_city,
                        "state": a.state,
                        "year_built": a.year_built,
                        "units": a.units,
                    }
                )
                for a in found[:limit]
            ],
            "searched_instead": searched,
            "note": (
                "Nothing on file is on that street in that city. rules_for_any_address "
                "can still answer it."
                if not found
                else "More than this matched; narrow the search rather than listing these."
                if len(found) > limit
                else None
            ),
        }
    )


async def _my_buildings(deps: Deps, args: dict) -> dict:
    places = await accounts.places(deps.session, deps.principal)
    return {
        "count": len(places),
        "buildings": [
            _drop_empty(
                {
                    "address_id": p.address_id,
                    "name": p.label,
                    "note": _clip(p.note, 200),
                    "street": p.street_address,
                    # The legal city is what rules attach to; the mailing city
                    # is only worth a line when the two disagree, which is the
                    # one case where saying both prevents a wrong answer.
                    "legal_city": p.legal_city,
                    "state": p.legal_state or p.state,
                    "mailing_city": p.postal_city if p.postal_city_differs else None,
                    "jurisdiction": (
                        None if p.jurisdiction_status == "resolved" else p.jurisdiction_status
                    ),
                    "year_built": p.year_built,
                    "units": p.units,
                    "documents_on_file": p.contract_count or None,
                    # When they saved it. An advocate's "my newest case" has no
                    # other answer, and nothing else here orders the list.
                    "saved": p.created_at.date(),
                }
            )
            for p in places[:25]
        ],
    }


async def _address_for(deps: Deps, address_id: str) -> Address | None:
    """One address, with its jurisdiction already loaded.

    The eager load is not an optimisation. Module B's evaluator reads
    `address.jurisdiction` to decide whether the legal city is verified, and a
    lazy relationship touched inside an async session raises `MissingGreenlet`
    rather than fetching - so a plain `session.get` makes every rule lookup
    fail. `_with_juris` in Module B's router is the same line for the same
    reason.
    """
    return (
        await deps.session.execute(
            select(Address)
            .options(selectinload(Address.jurisdiction))
            .where(Address.address_id == address_id.strip().upper())
        )
    ).scalar_one_or_none()


async def _rules_for_building(deps: Deps, args: dict) -> dict:
    # Module B's service is imported inside these handlers rather than at
    # module scope: it pulls in the rule compiler and the predicate machinery,
    # and the assistant router should not drag that into import time for a
    # deployment nobody chats to.
    address = await _address_for(deps, args["address_id"])
    if address is None:
        return {"error": f"No building {args['address_id']!r} is on file."}
    return await _answer_for(deps, address, _date_or(args.get("as_of"), deps.as_of))


async def _answer_for(deps: Deps, address: Address, as_of: dt.date) -> dict:
    """One building's outcomes, bucketed and capped.

    Shared by the two ways in - a building already on file, and one somebody
    typed - because the answer should read the same either way. What differs
    between them is said in the caller, not folded into the numbers.
    """
    from app.modules.address_lookup import service as lookups

    answer = await lookups.lookup_address(deps.session, address, as_of, persist=False)

    buckets: dict[str, list[dict]] = {
        "applies": [],
        "unknown": [],
        "coming": [],
        "superseded": [],
    }
    for outcome in answer.outcomes:
        if outcome.result == "applies":
            buckets["applies"].append(
                _drop_empty(
                    {
                        "rule": outcome.team_rule_id,
                        "title": outcome.title,
                        "category": outcome.category,
                        "requires": _clip(outcome.key_value, 160),
                        "citation": _clip(outcome.citation, 160),
                        "jurisdiction": outcome.jurisdiction,
                        "level": outcome.level,
                        "conflict": outcome.conflict_flag or None,
                    }
                )
            )
        elif outcome.result == "unknown":
            buckets["unknown"].append(
                _drop_empty(
                    {
                        "rule": outcome.team_rule_id,
                        "title": outcome.title,
                        # The field that would settle it. This is the whole
                        # value of an unknown: it names the next move.
                        "blocked_by": outcome.unresolved_fields or None,
                        "why": _clip(outcome.explanation, 220),
                    }
                )
            )
        elif outcome.result == "superseded":
            buckets["superseded"].append(
                _drop_empty(
                    {
                        "rule": outcome.team_rule_id,
                        "title": outcome.title,
                        "governed_instead_by": outcome.superseded_by,
                    }
                )
            )
        else:
            buckets["coming"].append(
                _drop_empty(
                    {
                        "rule": outcome.team_rule_id,
                        "title": outcome.title,
                        "status": outcome.result,
                    }
                )
            )

    # The agreements filed against this building, in the same breath as the
    # rules that govern them. Two calls used to be needed to say "your rent is
    # $1,850 and the rule that governs increases here says this", which is the
    # single most useful sentence this app can produce for somebody who has
    # filed a lease - and the one most likely to be skipped when it costs a
    # second round trip.
    filed = [
        _agreement(contract, address.address_id)
        for contract in await accounts.contracts(deps.session, deps.principal)
        if contract.place_id
        in {
            p.id
            for p in await accounts.places(deps.session, deps.principal)
            if p.address_id == address.address_id
        }
    ]

    caps = {"applies": 12, "unknown": 8, "coming": 4, "superseded": 4}
    shown = {name: rows[: caps[name]] for name, rows in buckets.items()}
    hidden = {
        name: len(rows) - caps[name] for name, rows in buckets.items() if len(rows) > caps[name]
    }
    return _drop_empty(
        {
            "address_id": answer.address_id,
            "street": address.street_address,
            "legal_city": answer.legal_city,
            "legal_state": answer.legal_state,
            "mailing_city": (
                address.postal_city
                if (answer.legal_city or "").lower() != address.postal_city.lower()
                else None
            ),
            "jurisdiction_resolved": answer.legal_city is not None,
            "as_of": as_of.isoformat(),
            "year_built": address.year_built,
            "units": address.units,
            "counts": {name: len(rows) for name, rows in buckets.items()},
            **shown,
            "not_listed": hidden or None,
            # Capped hard: a thirty-two unit building carries thirty-two
            # agreements and an answer about the law is not a rent roll.
            "agreements_on_file": filed[:6] or None,
            "agreements_not_listed": max(len(filed) - 6, 0) or None,
            "spans": "Call rule_source for the quoted text behind any rule id above.",
        }
    )


async def _rules_for_typed_address(deps: Deps, args: dict) -> dict:
    """Answer for a building nobody has on file, without putting it on file."""
    from app.modules.address_lookup import service as lookups

    try:
        address = await lookups.resolve_typed(args["address"])
    except lookups.TypedAddressError as exc:
        return {"error": str(exc)}

    juris = address.jurisdiction
    if not (juris and juris.legal_city):
        return {
            "typed": args["address"],
            "resolved": False,
            "error": (
                "The Census geocoder could not place that address, so there is no legal "
                "city and no rule can be attached to it. Check the street and the city, "
                "or try the building next door."
            ),
        }

    answer = await _answer_for(deps, address, _date_or(args.get("as_of"), deps.as_of))
    return {
        **answer,
        "on_file": False,
        "note": (
            "Not one of the buildings on file, so it carries no year built and no unit "
            "count - any rule turning on those answers unknown. The legal city is the "
            "geocoder's own and is as reliable as for any other address."
        ),
    }


async def _rule_source(deps: Deps, args: dict) -> dict:
    rule = (
        await deps.session.execute(select(Rule).where(Rule.team_rule_id == args["rule_id"].strip()))
    ).scalar_one_or_none()
    if rule is None:
        return {"error": f"No rule {args['rule_id']!r} exists in this corpus."}
    return _drop_empty(
        {
            "rule": rule.team_rule_id,
            "title": rule.title,
            "jurisdiction": rule.jurisdiction,
            "level": rule.level,
            "category": rule.category,
            "status": rule.status,
            "effective_date": rule.effective_date,
            "requirement": _clip(rule.requirement, 400),
            "exemptions": _clip(rule.exemptions, 300),
            # Verified verbatim against the source document before this row was
            # written, and re-verified on import. Quote it as it stands.
            "quoted_span": _clip(rule.quoted_span, SPAN_CHARS),
            "citation": rule.citation,
            "source_url": rule.source_url,
            "source_document": rule.source_doc_id,
            "conflict_note": _clip(rule.conflict_note, 240),
        }
    )


async def _missing_facts(deps: Deps, args: dict) -> dict:
    from app.modules.address_lookup import service as lookups

    places = await accounts.places(deps.session, deps.principal)
    if not places:
        return {"buildings": 0, "note": "Nothing is saved yet, so nothing is blocked."}

    where: dict[str, set[str]] = {}
    # How many unknown answers each field appears in - not how many buildings.
    # The two differ a lot and only the first one ranks the work: a field that
    # blocks forty answers at one building is a bigger job than one that blocks
    # a single answer at six, and the frontend's `blockingFacts` orders it the
    # same way for the same reason.
    counts: dict[str, int] = {}
    answers = 0
    # An unknown that names no field is a different problem, and conflating the
    # two makes this queue read as mostly unexplained. A missing year built is
    # somebody's to go and find; a coverage clause that has not been translated
    # into a checkable condition is nobody's to find at all - it is the
    # corpus's own review queue, and no fact about the building will move it.
    awaiting_review = 0
    for place in places[:25]:
        address = await _address_for(deps, place.address_id)
        if address is None:
            continue
        answer = await lookups.lookup_address(deps.session, address, deps.as_of, persist=False)
        for outcome in answer.outcomes:
            if outcome.result != "unknown":
                continue
            answers += 1
            if not outcome.unresolved_fields:
                awaiting_review += 1
                continue
            for field in outcome.unresolved_fields:
                counts[field] = counts.get(field, 0) + 1
                where.setdefault(field, set()).add(place.address_id)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "buildings": len(places),
        "unknown_answers": answers,
        "blocked_by_a_missing_fact": answers - awaiting_review,
        "blocked_by_rule_review": awaiting_review,
        "ranked_by": "answers_blocked",
        "blocking": [
            {
                "field": field,
                "answers_blocked": blocked,
                "buildings": len(where[field]),
                "address_ids": sorted(where[field])[:10],
            }
            for field, blocked in ranked[:8]
        ],
        "note": (
            "Only blocked_by_a_missing_fact is work they can do - the fields below are "
            "facts about their buildings. blocked_by_rule_review is this corpus's own "
            "queue: those rules' coverage clauses have not been translated into "
            "checkable conditions, and no fact about a building will settle them."
        ),
    }


async def _change_cases(deps: Deps, args: dict) -> dict:
    from app.modules.change_tracking import service as changes

    address_id = (args.get("address_id") or "").strip().upper() or None
    results = []
    for result in await changes.run_tests(deps.session, changes.load_tests()):
        # A blocked case has no answer, so it is reported whatever the address:
        # leaving it out would read as "this case does not move that address".
        if result.status == "blocked":
            results.append({"case": result.test_id, "error": result.blocked_reason})
            continue
        if address_id and address_id not in result.affected_address_ids:
            continue
        results.append(
            _drop_empty(
                {
                    "case": result.test_id,
                    "title": result.title,
                    "type": result.type,
                    "as_of": result.as_of,
                    "addresses_moved": len(result.affected_address_ids),
                    "conflicts_flagged": len(result.conflict_flag_address_ids) or None,
                    "notes": _clip(result.notes, 320),
                    "rules_resolved": None if result.rules_resolved else False,
                    "partly_answered": (
                        [_clip(w, 200) for w in result.warnings]
                        if result.status == "partial"
                        else None
                    ),
                }
            )
        )
    return _drop_empty(
        {
            "for_address": address_id,
            "cases": results,
            "note": (
                "No supplied change case moves an answer at that address."
                if address_id and not results
                else None
            ),
        }
    )


async def _stock_coverage(deps: Deps, args: dict) -> dict:
    session = deps.session
    # Joined to `addresses` and filtered, like every other figure here: this
    # is coverage *of what was imported*, and a typed address carries a jurisdiction
    # row like any other.
    verified = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .join(Address, Address.address_id == AddressJurisdiction.address_id)
            .where(Address.imported, AddressJurisdiction.legal_city.isnot(None))
        )
    ).scalar_one()
    total = (
        await session.execute(select(func.count()).select_from(Address).where(Address.imported))
    ).scalar_one()
    no_year = (
        await session.execute(
            select(func.count())
            .select_from(Address)
            .where(Address.imported, Address.year_built.is_(None))
        )
    ).scalar_one()
    no_units = (
        await session.execute(
            select(func.count())
            .select_from(Address)
            .where(Address.imported, Address.units.is_(None))
        )
    ).scalar_one()
    corrected = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .join(Address, Address.address_id == AddressJurisdiction.address_id)
            .where(
                Address.imported,
                AddressJurisdiction.legal_city.isnot(None),
                func.lower(AddressJurisdiction.legal_city) != func.lower(Address.postal_city),
            )
        )
    ).scalar_one()
    by_state = {
        str(state): n
        for state, n in await session.execute(
            select(Address.state, func.count()).where(Address.imported).group_by(Address.state)
        )
    }
    cities = [
        {"legal_city": str(city), "addresses": n}
        for city, n in await session.execute(
            select(AddressJurisdiction.legal_city, func.count())
            .join(Address, Address.address_id == AddressJurisdiction.address_id)
            .where(Address.imported, AddressJurisdiction.legal_city.isnot(None))
            .group_by(AddressJurisdiction.legal_city)
            .order_by(func.count().desc())
            .limit(12)
        )
    ]
    rules = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    return {
        "addresses": total,
        "jurisdiction_verified": verified,
        "jurisdiction_unverified": total - verified,
        "mailing_city_differs_from_legal": corrected,
        "missing_year_built": no_year,
        "missing_units": no_units,
        "rules_in_corpus": rules,
        "by_state": dict(sorted(by_state.items())),
        "largest_legal_cities": cities,
    }


def _agreement(contract, address_id: str | None = None) -> dict:
    """One agreement, as much of it as exists, and no more.

    Every field here was typed in by the person who filed it. Nothing was read
    off the file and nothing was verified against it, which is why the rent is
    carried as the figure they gave rather than as an amount: it goes *beside*
    the rule that governs it, never into it.
    """
    return _drop_empty(
        {
            "document_id": contract.id,
            "address_id": address_id,
            "unit": contract.unit_label,
            "filename": contract.filename,
            "kind": contract.kind,
            "pages": contract.page_count,
            "term_starts": contract.starts_on,
            "term_ends": contract.ends_on,
            "monthly_rent_as_typed": (
                None
                if contract.monthly_rent_cents is None
                else f"${contract.monthly_rent_cents / 100:,.2f} a month"
            ),
            "note": _clip(contract.note, 200),
            "filed_on": contract.created_at.date(),
        }
    )


async def _my_documents(deps: Deps, args: dict) -> dict:
    """The agreements on file, with the gaps in them named.

    What makes this worth more than a list is the second half: a portfolio's
    real question is not "what have I filed" but "which lets have no agreement,
    and which terms run out next". Both are arithmetic over dates and counts,
    which is allowed here - what is forbidden is arithmetic over somebody's
    rent, and no figure in this result is ever a term in a sum.
    """
    filed = await accounts.contracts(deps.session, deps.principal)
    places = await accounts.places(deps.session, deps.principal)
    by_place = {p.id: p for p in places}

    wanted = (args.get("address_id") or "").strip().upper() or None
    if wanted:
        ids = {p.id for p in places if p.address_id == wanted}
        if not ids:
            return {"error": f"No building {wanted} is on this account."}
        filed = [c for c in filed if c.place_id in ids]

    rows = [_agreement(c, getattr(by_place.get(c.place_id), "address_id", None)) for c in filed]
    # Soonest to end first, because an agreement with a date on it is the one
    # somebody has to act on; the undated ones are not urgent, they are
    # incomplete, and they are counted separately below.
    dated = sorted((r for r in rows if r.get("term_ends")), key=lambda r: r["term_ends"])
    undated = [r for r in rows if not r.get("term_ends")]

    buildings_with = {c.place_id for c in filed}
    missing = [
        p.address_id
        for p in places
        if p.id not in buildings_with and (not wanted or p.address_id == wanted)
    ]
    today = deps.as_of
    ending_soon = [r for r in dated if r["term_ends"] and (r["term_ends"] - today).days <= 180]

    shown = (dated + undated)[:25]
    return _drop_empty(
        {
            "for_building": wanted,
            "count": len(filed),
            "buildings_with_an_agreement": len(buildings_with),
            "buildings_with_none": len(missing),
            # Named only when the list is short enough to act on; past that the
            # count is the finding and the buildings page is the place to work.
            "buildings_with_none_ids": sorted(missing)[:10] if missing else None,
            "ending_within_180_days": len(ending_soon),
            "no_term_recorded": len(undated),
            "agreements": shown,
            "not_listed": len(rows) - len(shown) or None,
            "reading": (
                "Every field here was typed in by whoever filed the agreement. The file "
                "itself has not been read and nothing in it is verified, so quote these "
                "as what they told you, put a rent beside the rule that governs it, and "
                "never compute with either."
            ),
        }
    )


def _date_or(value: Any, fallback: dt.date) -> dt.date:
    if not value:
        return fallback
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return fallback


# ---------------------------------------------------------------------------
# The roster. One entry per tool, in name order - see the module docstring.
# ---------------------------------------------------------------------------

_ADDRESS_ID = {
    "type": "string",
    "description": "The id of a building on file, e.g. A0132.",
}
_MESSAGE = {
    "type": "string",
    "description": "One sentence, shown above the control. Say what you need and why.",
}

ALL: tuple[Tool, ...] = (
    Tool(
        name="ask_for_agreement_details",
        label="Asking for the term and the rent",
        description=(
            "Render a form for the term, the rent, the unit label or a note on an "
            "agreement already on file. These are self-reported and nothing verifies "
            "them against the file. Get document_id from my_documents."
        ),
        properties={
            "message": _MESSAGE,
            "document_id": {"type": "integer", "description": "From my_documents."},
            "fields": {
                "type": "array",
                "description": "Which fields to show.",
                "items": {
                    "type": "string",
                    "enum": ["unit_label", "starts_on", "ends_on", "monthly_rent", "note"],
                },
            },
        },
        required=("message", "document_id", "fields"),
    ),
    Tool(
        name="ask_for_document",
        label="Asking for the document",
        description=(
            "Render a file picker that files a tenancy agreement against one of the "
            "person's own saved buildings. The file is stored and never read - do not "
            "promise to extract anything from it."
        ),
        properties={"message": _MESSAGE, "address_id": _ADDRESS_ID},
        required=("message", "address_id"),
    ),
    Tool(
        name="ask_to_add_building",
        label="Asking which building",
        description=(
            "Render a search box that saves a building to the person's account. Use it "
            "when they have none saved, or name one that is not on file yet. It searches "
            "the buildings this account already holds; a renter may also keep an address "
            "that is not among them, typed as 'street, city, state'. To answer a question "
            "about an address without saving it, use rules_for_any_address instead."
        ),
        properties={
            "message": _MESSAGE,
            "query": {
                "type": "string",
                "description": (
                    "Prefill for the search box. If they gave a whole address, put it "
                    "in whole - 'street, city, state' - and the control searches the "
                    "book on its street while offering to keep the address itself. "
                    "Otherwise a street OR a city, never both, because a partial "
                    "search matches one field at a time. Empty if they named neither."
                ),
            },
        },
        required=("message", "query"),
    ),
    Tool(
        name="ask_to_pick_building",
        label="Asking which of their buildings",
        description=(
            "Render a picker over the buildings the person has already saved. Use it "
            "when a question needs one building and it is not clear which."
        ),
        properties={"message": _MESSAGE},
        required=("message",),
    ),
    Tool(
        name="change_cases",
        label="Replaying the change cases",
        description=(
            "The supplied law-change cases (T1-T5), replayed by this deployment's own "
            "evaluator rather than looked up. Pass address_id to keep only the cases "
            "that move an answer there, or empty for all five."
        ),
        properties={
            "address_id": {
                "type": "string",
                "description": "The id of a building on file, or empty for all cases.",
            }
        },
        required=("address_id",),
        run=_change_cases,
    ),
    Tool(
        name="missing_facts",
        label="Finding what is blocking answers",
        description=(
            "Across the person's saved buildings: which missing field is blocking the "
            "most rule answers, ranked. The work queue."
        ),
        properties={},
        run=_missing_facts,
    ),
    Tool(
        name="my_buildings",
        label="Reading their buildings",
        description="The buildings this person has saved, with the facts rules turn on.",
        properties={},
        run=_my_buildings,
    ),
    Tool(
        name="my_documents",
        label="Reading what is on file",
        description=(
            "The tenancy agreements this person has filed: unit, term, the rent they "
            "typed in, and which building each is against. Also which of their "
            "buildings have no agreement at all and how many terms end within six "
            "months, which is usually the actual question. Pass address_id for one "
            "building, or empty for all of them. The files themselves are never read "
            "and nothing in them is verified - quote these as what they told you."
        ),
        properties={
            "address_id": {
                "type": "string",
                "description": "One building's id, or empty for every agreement on file.",
            }
        },
        required=("address_id",),
        run=_my_documents,
    ),
    Tool(
        name="rule_source",
        label="Fetching the source text",
        description=(
            "The verified quoted span, citation and source document for one rule id. "
            "Call this before quoting a rule; the spans are deliberately absent from "
            "rules_for_building."
        ),
        properties={"rule_id": {"type": "string", "description": "e.g. r-0042."}},
        required=("rule_id",),
        run=_rule_source,
    ),
    Tool(
        name="rules_for_any_address",
        label="Checking an address that is not on file",
        description=(
            "Evaluate every rule against an address nobody has on file - one the person "
            "typed or pasted. Give it as 'street, city, state'. It is geocoded to its "
            "legal city and answered like any other building, except that it carries no "
            "year built and no unit count, so a rule turning on those answers unknown. "
            "Nothing is saved. Use rules_for_building for a building already on file."
        ),
        properties={
            "address": {
                "type": "string",
                "description": "street, city, state - e.g. '415 Mission St, San Francisco, CA'.",
            },
            "as_of": {
                "type": "string",
                "description": "YYYY-MM-DD, or empty for the deployment's query date.",
            },
        },
        required=("address", "as_of"),
        run=_rules_for_typed_address,
    ),
    Tool(
        name="rules_for_building",
        label="Checking which rules reach it",
        description=(
            "Evaluate every rule against one address on a date: what applies, what is "
            "unknown and which field blocked it, what is superseded, and what is not "
            "yet in force. Works for any building on file, saved or not. It also "
            "returns the agreements filed against that building, so a rent can be put "
            "beside the rule that governs it without a second call. Quoted spans are "
            "not included - use rule_source."
        ),
        properties={
            "address_id": _ADDRESS_ID,
            "as_of": {
                "type": "string",
                "description": "YYYY-MM-DD, or empty for the deployment's query date.",
            },
        },
        required=("address_id", "as_of"),
        run=_rules_for_building,
    ),
    Tool(
        name="search_addresses",
        label="Searching the buildings on file",
        description=(
            "Find a building among the ones this account holds, by street or city. "
            "Returns the id every other tool here takes. For an address nobody has on "
            "file, use rules_for_any_address."
        ),
        properties={
            "query": {
                "type": "string",
                "description": (
                    "Part of a street, or a city - one or the other. The two are "
                    "matched separately, so a street and a city together match nothing."
                ),
            },
            "limit": {"type": "integer", "description": "1-15. Default 8."},
        },
        required=("query",),
        run=_search_addresses,
    ),
    Tool(
        name="show_view",
        label="Offering a page",
        description=(
            "Offer a link to a page of this app, as a card in the chat. Paths only: "
            "/places, /rules, /addresses, /changes, /settings."
        ),
        properties={
            "message": _MESSAGE,
            "href": {"type": "string", "description": "An app path, with optional query."},
            "label": {"type": "string", "description": "The button text, 2-5 words."},
        },
        required=("message", "href", "label"),
        waits=False,
    ),
    Tool(
        name="stock_coverage",
        label="Measuring the stock",
        description=(
            "Coverage across the whole imported stock: how many resolve to a "
            "verified legal jurisdiction, how many are missing the facts rules turn "
            "on, and where the mailing city is not the legal one."
        ),
        properties={},
        run=_stock_coverage,
    ),
)

BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL}


# Which tools each role gets, and the absences are the statement.
#
# An agency holds no addresses and has nowhere in this app to keep one, so they
# get no tool that would offer to save or upload anything - the assistant
# cannot propose it because it has no way to. A renter gets no `missing_facts`:
# a work queue across buildings is meaningless for one home, and `pick` is
# meaningless for one building.
_ROSTERS: dict[Role, tuple[str, ...]] = {
    Role.renter: (
        "rules_for_any_address",
        "ask_for_agreement_details",
        "ask_for_document",
        "ask_to_add_building",
        "change_cases",
        "my_buildings",
        "my_documents",
        "rule_source",
        "rules_for_building",
        "search_addresses",
        "show_view",
    ),
    Role.provider: (
        "rules_for_any_address",
        "ask_for_agreement_details",
        "ask_for_document",
        "ask_to_add_building",
        "ask_to_pick_building",
        "change_cases",
        "missing_facts",
        "my_buildings",
        "my_documents",
        "rule_source",
        "rules_for_building",
        "search_addresses",
        "show_view",
    ),
    Role.agency: (
        "rules_for_any_address",
        "change_cases",
        "rule_source",
        "rules_for_building",
        "search_addresses",
        "show_view",
        "stock_coverage",
    ),
    Role.advocate: (
        "rules_for_any_address",
        "ask_for_agreement_details",
        "ask_for_document",
        "ask_to_add_building",
        "ask_to_pick_building",
        "change_cases",
        "missing_facts",
        "my_buildings",
        "my_documents",
        "rule_source",
        "rules_for_building",
        "search_addresses",
        "show_view",
    ),
}


def roster(role: Role) -> tuple[Tool, ...]:
    """This role's tools, in name order.

    Four rosters means four cache prefixes, one per role, each stable for the
    life of the deployment - which is right, because nobody's role changes
    mid-conversation.
    """
    return tuple(BY_NAME[name] for name in sorted(_ROSTERS[role]))


def wire(role: Role) -> list[dict]:
    return [tool.wire() for tool in roster(role)]
