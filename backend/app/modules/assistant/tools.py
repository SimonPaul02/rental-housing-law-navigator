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


async def _search_addresses(deps: Deps, args: dict) -> dict:
    needle = f"%{args['query'].strip()}%"
    limit = min(int(args.get("limit") or 8), 15)
    rows = (
        await deps.session.execute(
            select(Address)
            .where(
                or_(
                    Address.street_address.ilike(needle),
                    Address.postal_city.ilike(needle),
                )
            )
            .order_by(Address.address_id)
            .limit(limit + 1)
        )
    ).scalars()
    found = list(rows)
    note = (
        "More than this matched; narrow the search rather than listing these."
        if len(found) > limit
        else None
    )
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
            "note": note,
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
    # Imported here rather than at module scope: Module B's service pulls in
    # the rule compiler and the predicate machinery, and the assistant router
    # should not drag that into import time for a deployment nobody chats to.
    from app.modules.address_lookup import service as lookups

    address = await _address_for(deps, args["address_id"])
    if address is None:
        return {"error": f"No address {args['address_id']!r} is in this sample."}

    as_of = _date_or(args.get("as_of"), deps.as_of)
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
            "spans": "Call rule_source for the quoted text behind any rule id above.",
        }
    )


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
    for test in changes.load_tests():
        try:
            result = await changes.run_test(deps.session, test, persist=False)
        except changes.ChangeInputError as exc:
            results.append({"case": test.test_id, "error": str(exc)})
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
    verified = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .where(AddressJurisdiction.legal_city.isnot(None))
        )
    ).scalar_one()
    total = (await session.execute(select(func.count()).select_from(Address))).scalar_one()
    no_year = (
        await session.execute(
            select(func.count()).select_from(Address).where(Address.year_built.is_(None))
        )
    ).scalar_one()
    no_units = (
        await session.execute(
            select(func.count()).select_from(Address).where(Address.units.is_(None))
        )
    ).scalar_one()
    corrected = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .join(Address, Address.address_id == AddressJurisdiction.address_id)
            .where(
                AddressJurisdiction.legal_city.isnot(None),
                func.lower(AddressJurisdiction.legal_city) != func.lower(Address.postal_city),
            )
        )
    ).scalar_one()
    by_state = {
        str(state): n
        for state, n in await session.execute(
            select(Address.state, func.count()).group_by(Address.state)
        )
    }
    cities = [
        {"legal_city": str(city), "addresses": n}
        for city, n in await session.execute(
            select(AddressJurisdiction.legal_city, func.count())
            .where(AddressJurisdiction.legal_city.isnot(None))
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


async def _my_documents(deps: Deps, args: dict) -> dict:
    filed = await accounts.contracts(deps.session, deps.principal)
    places = {p.id: p for p in await accounts.places(deps.session, deps.principal)}
    return {
        "count": len(filed),
        "documents": [
            _drop_empty(
                {
                    "document_id": c.id,
                    "address_id": getattr(places.get(c.place_id), "address_id", None),
                    "unit": c.unit_label,
                    "filename": c.filename,
                    "kind": c.kind,
                    "term_starts": c.starts_on,
                    "term_ends": c.ends_on,
                    # Self-reported, never read off the file, never used in a
                    # calculation. The system prompt forbids the arithmetic.
                    "monthly_rent_cents": c.monthly_rent_cents,
                    "note": _clip(c.note, 200),
                }
            )
            for c in filed[:30]
        ],
    }


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
    "description": "A sample address id, e.g. A0132.",
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
            "when they have none saved, or name one that is not theirs yet. Addresses "
            "come from the 500-row sample only: a hand-typed address has no year built "
            "or unit count, so nearly every rule would answer unknown."
        ),
        properties={
            "message": _MESSAGE,
            "query": {
                "type": "string",
                "description": (
                    "Prefill for the search box. A street OR a city, never both - the "
                    "search matches one field, so 'Taylor St San Francisco' finds "
                    "nothing while 'Taylor St' finds it. Empty if they named neither."
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
                "description": "A sample address id, or empty for all cases.",
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
            "The tenancy agreements this person has filed. Metadata only - the files "
            "themselves are never read."
        ),
        properties={},
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
        name="rules_for_building",
        label="Checking which rules reach it",
        description=(
            "Evaluate every rule against one address on a date: what applies, what is "
            "unknown and which field blocked it, what is superseded, and what is not "
            "yet in force. Works for any of the 500 sample addresses, saved or not. "
            "Quoted spans are not included - use rule_source."
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
        label="Searching the sample",
        description=(
            "Find addresses in the 500-row sample by street or city. Returns the id "
            "every other tool here takes."
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
            "Coverage across the whole 500-address sample: how many resolve to a "
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
        "change_cases",
        "rule_source",
        "rules_for_building",
        "search_addresses",
        "show_view",
        "stock_coverage",
    ),
    Role.advocate: (
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
