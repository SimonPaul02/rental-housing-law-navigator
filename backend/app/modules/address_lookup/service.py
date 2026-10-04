"""Module B: resolve a jurisdiction, then decide which rules apply."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import secrets
from collections import Counter
from dataclasses import asdict
from threading import Lock
from time import monotonic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.models import Address, AddressJurisdiction, Lookup, Rule
from app.modules.address_lookup.adapters.address_files import read_review_overrides_csv
from app.modules.address_lookup.adapters.property_facts import from_db_address
from app.modules.address_lookup.address_resolution.census import CachedGeocoder, CensusGeocoder
from app.modules.address_lookup.address_resolution.models import AddressInput, ResolvedAddress
from app.modules.address_lookup.address_resolution.resolver import (
    apply_review_overrides,
    resolve_addresses,
)
from app.modules.address_lookup.rule_adapter import adapters as rule_adapters
from app.modules.address_lookup.rule_adapter.models import (
    COMPILER_VERSION,
    CompiledRule,
    Relation,
    rule_version_hash,
)
from app.modules.address_lookup.rule_evaluation import predicates as pred
from app.modules.address_lookup.rule_evaluation.base import evaluate_base
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult, Decision, Ternary
from app.modules.address_lookup.rule_evaluation.explanations import explain
from app.modules.address_lookup.rule_evaluation.export import (
    SubmissionInvalid,
    to_submission,
    validate_submission,
)
from app.modules.address_lookup.rule_evaluation.interactions import resolve_interactions
from app.modules.address_lookup.schemas import (
    BlockingFact,
    CategoryCell,
    LookupResponse,
    Portfolio,
    PortfolioBuilding,
    PortfolioRule,
    PortfolioTotals,
    RuleOutcome,
)
from app.modules.address_lookup.status import VERIFIED_METHODS

log = logging.getLogger(__name__)
_GEOCODE_LOCK = Lock()


def input_from_db(address: Address) -> AddressInput:
    return AddressInput(
        address_id=address.address_id,
        street_address=address.street_address,
        postal_city=address.postal_city,
        state=address.state,
        zip=address.zip or "",
        source_dataset=address.source_dataset or "",
        retrieved_at=address.retrieved_at or "",
    )


def merge_zip_evidence(existing: dict, result: ResolvedAddress) -> dict:
    """Backfill ZIP evidence without replacing a jurisdiction or its review history."""
    previous_input = existing.get("input") or {}
    current = result.input
    for field in ("street_address", "postal_city", "state", "zip"):
        if (
            str(previous_input.get(field) or "").strip()
            != str(getattr(current, field) or "").strip()
        ):
            raise ValueError(f"Address {result.address_id} changed since jurisdiction resolution")
    updated = dict(existing)
    updated["zip_assessment"] = asdict(result.zip_assessment)
    updated["accepted_endpoints"] = [asdict(item) for item in result.accepted_endpoints]
    return updated


def evidence_for(address: Address) -> pred.AddressEvidence:
    """Everything the evaluator may know about one address, with status kept.

    The point of carrying `FactValue` rather than a bare value is that
    `not_supplied`, `invalid` and `conflicted` are different answers. Flattening
    all three to `None` loses the reason, and the reason is what the explanation
    has to say out loud.
    """
    juris = address.jurisdiction
    verified = bool(juris and juris.method in VERIFIED_METHODS)
    record = from_db_address(address)

    def fact(name: str) -> pred.FactValue:
        held = getattr(record, name, None)
        if held is None:
            return pred.FactValue(None, "not_supplied")
        provenance = getattr(held, "provenance", None)
        return pred.FactValue(
            value=held.value,
            status=held.status,
            scope=getattr(record, "record_scope", "source_address_row"),
            provenance=getattr(provenance, "source_dataset", None) if provenance else None,
        )

    return pred.AddressEvidence(
        address_id=address.address_id,
        legal_city=pred.FactValue(
            juris.legal_city if verified else None,
            "present" if verified and juris.legal_city else "not_supplied",
        ),
        legal_state=pred.FactValue(
            (juris.legal_state if juris and juris.legal_state else address.state),
            "present" if (juris and juris.legal_state) or address.state else "not_supplied",
        ),
        units=fact("units"),
        year_built=fact("year_built"),
        certificate_of_occupancy_date=fact("certificate_of_occupancy_date"),
        use_code=fact("use_code"),
        jurisdiction_method=(juris.method if juris else None),
        jurisdiction_note=(juris.note if juris else None),
    )


# A rule is compiled once per version and kept, so evaluating five hundred
# addresses does not retranslate the same prose five hundred times.
_COMPILED: dict[str, CompiledRule] = {}
_COMPILED_LOCK = Lock()


def compiled_for(record: Rule) -> CompiledRule:
    version = rule_version_hash(record)
    document = getattr(record, "__dict__", {}).get("document")
    source_text = document.body if document is not None else None
    from app.modules.address_lookup.rule_adapter import compiler

    source_hash = compiler.content_hash(source_text)
    key = f"{record.team_rule_id}@{version}@{source_hash}@{COMPILER_VERSION}"
    with _COMPILED_LOCK:
        hit = _COMPILED.get(key)
    if hit is not None:
        return hit

    store = rule_adapters.store()
    compiled = store.get(record.team_rule_id, version, source_hash)
    if compiled is None:
        compiled, _ = compiler.compile_rule(record, source_text=source_text)
    compiled = store.apply_reviews(compiled)
    with _COMPILED_LOCK:
        _COMPILED[key] = compiled
    return compiled


def clear_compiled_cache() -> None:
    """Drop the compiled rules - and the roll-ups computed from them.

    A portfolio is an answer about a set of buildings *and* a set of rules, so
    a re-import that changes the rules has to take the roll-ups with it. The
    TTL would get there eventually; five minutes of a page insisting the old
    answer is current is five minutes too many.
    """
    clear_portfolio_cache()
    with _COMPILED_LOCK:
        _COMPILED.clear()


async def compiled_rules(
    session: AsyncSession,
) -> tuple[list[Rule], dict[str, CompiledRule], list[Relation]]:
    """Every rule, its compiled form, and the reviewed relations between them."""
    rows = await session.execute(
        select(Rule).options(selectinload(Rule.document)).order_by(Rule.team_rule_id)
    )
    records = list(rows.scalars().all())
    store = rule_adapters.store()
    sources = {
        r.source_doc_id: r.document.body
        for r in records
        if r.source_doc_id and r.document and r.document.body
    }
    compiled, relations, fresh = rule_adapters.compile_all(
        records, review_store=store, sources=sources, persist=False
    )
    if fresh:
        store.save()
        clear_compiled_cache()
    return records, compiled, relations


def _value_period_note(rule: CompiledRule, as_of: dt.date) -> str | None:
    period = rule.key_value_period
    if period and not period.start <= as_of <= period.end:
        return (
            f"The quoted numerical value covers {period.start.isoformat()} through "
            f"{period.end.isoformat()}; its value for {as_of.isoformat()} is not established here."
        )
    return None


def _outcome_from(decision: Decision, record: Rule, compiled: CompiledRule) -> RuleOutcome:
    return RuleOutcome(
        team_rule_id=decision.team_rule_id,
        result=str(decision.result),
        explanation=decision.explanation,
        conflict_flag=decision.conflict_flag or bool(record.conflict_flag),
        unresolved_fields=list(decision.base.unresolved_fields),
        in_jurisdiction=decision.base.geography is not Ternary.false,
        category=record.category,
        jurisdiction=record.jurisdiction,
        level=record.level,
        status=record.status,
        title=record.title,
        key_value=(
            None
            if compiled.effective_date_unresolved
            or _value_period_note(compiled, decision.base.as_of)
            else record.key_value
        ),
        citation=record.citation,
        source_url=record.source_url,
        quoted_span=record.quoted_span,
        superseded_by=decision.superseded_by,
        issue_key=decision.base.issue_key,
        checks=[c.to_json() for c in (*decision.base.checks, *decision.interaction_checks)],
    )


def decide_for_address(
    records: list[Rule],
    compiled: dict[str, CompiledRule],
    relations: list[Relation],
    evidence: pred.AddressEvidence,
    as_of: dt.date,
) -> list[Decision]:
    """Base decisions for every rule, then the interaction pass across them."""
    bases = [evaluate_base(compiled[r.team_rule_id], evidence, as_of) for r in records]
    decisions = resolve_interactions(bases, relations)
    for decision in decisions:
        decision.explanation = explain(decision)
        if note := _value_period_note(compiled[decision.team_rule_id], decision.base.as_of):
            decision.explanation += " " + note
    return decisions


# ------------------------------------------------------------- resolution ---
def _resolve_batch(addresses: list[AddressInput]) -> list[ResolvedAddress]:
    """Run the synchronous, cached resolver once per batch in a worker thread."""
    cache = settings.data_root / "data" / "census_geocode_cache.jsonl"
    live = CensusGeocoder(
        timeout=settings.geocoder_timeout_seconds,
        url=settings.census_geocoder_url,
    )
    with _GEOCODE_LOCK:
        results = resolve_addresses(addresses, CachedGeocoder(cache, live))
        overrides_path = settings.data_root / "data" / "address_overrides.csv"
        if overrides_path.exists():
            ids = {address.address_id for address in addresses}
            overrides = [
                item for item in read_review_overrides_csv(overrides_path) if item.address_id in ids
            ]
            results = apply_review_overrides(results, overrides)
        return results


# ------------------------------------------------- an address somebody typed ---
#: Comma parts of a typed address that name neither a street nor a city.
_COUNTRY = re.compile(r"^(?:usa|u\.s\.a\.|united states)$", re.IGNORECASE)
#: "CA", "CA 94105" or "94105" - the state and postcode part, in either order.
_STATE_ZIP = re.compile(r"^([A-Za-z]{2})?\s*(\d{5}(?:-\d{4})?)?$")


class TypedAddressError(ValueError):
    """What was typed is not an address this can resolve."""


def parse_typed(address: str) -> tuple[str, str, str, str | None]:
    """Split "415 Mission St, San Francisco, CA 94105, USA" into its parts.

    Commas, because that is how every autofill, map app and letterhead writes
    an address, and guessing where a street ends without them is a worse bet
    than asking. The country is dropped; the state is required, because the
    resolver needs it and city names repeat across states.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    parts = [p for p in parts if not _COUNTRY.match(p)]
    if len(parts) < 3:
        raise TypedAddressError(
            "Write it as street, city, state - for example '415 Mission St, San Francisco, CA'."
        )
    tail = _STATE_ZIP.match(parts[-1])
    if tail is None or not tail.group(1):
        raise TypedAddressError(
            f"{parts[-1]!r} is not a state. End with the two-letter state, "
            "for example 'San Francisco, CA'."
        )
    return (parts[0].upper(), parts[1].title(), tail.group(1).upper(), tail.group(2))


async def resolve_typed(address: str) -> Address:
    """An address somebody typed, resolved but not stored.

    Returned transient on purpose, because the two callers want different
    things from it: the assistant answers a question about a building nobody
    is keeping, and `accounts.add_typed_place` adds the same object to a
    session. Nothing here writes, so asking about an address never leaves a
    row behind.

    It carries no year built and no unit count, and none is invented. Every
    rule that turns on one answers "unknown" naming the field, which is the
    honest result rather than a degraded one - and in this corpus it costs
    very little, because almost every unknown is waiting on rule review
    instead.
    """
    street, city, state, postcode = parse_typed(address)
    row = Address(
        # Upper case so that normalising an id - which callers do, because a
        # model writes `a0001` as readily as `A0001` - leaves it unchanged. Not
        # sequential, because `/lookup/{id}` answers for these too and a
        # countable id would let somebody walk the list of places people live.
        address_id=f"U{secrets.token_hex(6).upper()}",
        street_address=street,
        postal_city=city,
        state=state,
        zip=postcode,
        year_built=None,
        units=None,
        source_dataset="typed in by the person who saved it",
        imported=False,
        # Assigned so the relationships count as loaded: a new row has neither,
        # and reading an unloaded one inside an async session raises rather
        # than fetching.
        jurisdiction=None,
        zip_reviews=[],
    )
    [result] = await asyncio.to_thread(_resolve_batch, [input_from_db(row)])
    row.jurisdiction = _jurisdiction(result, AddressJurisdiction(address_id=row.address_id))
    return row


def _jurisdiction(result: ResolvedAddress, row: AddressJurisdiction) -> AddressJurisdiction:
    """Copy one resolver decision onto a jurisdiction row, stored or not."""
    row.legal_city = result.legal_city
    row.legal_state = result.legal_state
    row.county = result.legal_county
    row.method = result.resolution_method
    row.matched_address = result.matched_address
    row.latitude = result.latitude
    row.longitude = result.longitude
    row.place_geoid = result.city_geoid
    row.confidence = None  # No calibrated probability is supplied by Census.
    row.note = "; ".join(result.warnings) or None
    row.resolution_evidence = asdict(result)
    return row


async def resolve_many(
    session: AsyncSession, addresses: list[Address]
) -> list[AddressJurisdiction]:
    """Persist resolver decisions, including unresolved records needing review."""
    inputs = [input_from_db(address) for address in addresses]
    results = await asyncio.to_thread(_resolve_batch, inputs)
    rows: list[AddressJurisdiction] = []
    by_id = {address.address_id: address for address in addresses}
    for result in results:
        address = by_id[result.address_id]
        row = _jurisdiction(
            result, address.jurisdiction or AddressJurisdiction(address_id=result.address_id)
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


# ----------------------------------------------------------------- lookup ---
def evaluate_rule_for_address(
    rule: Rule, evidence: pred.AddressEvidence, as_of: dt.date
) -> RuleOutcome:
    """The decision for one (rule, address, date) triple.

    Kept as a single-rule entry point for Module C and the detail view. It runs
    the same compiled rule and the same evaluator as a full lookup; what it
    cannot do is the interaction pass, which needs every rule for the address -
    so a rule that would be superseded reports its base result here.
    """
    compiled = compiled_for(rule)
    base = evaluate_base(compiled, evidence, as_of)
    decision = Decision(base=base, result=base.result, conflict_flag=base.conflict_flag)
    decision.conflict_reason = base.conflict_reason
    decision.explanation = explain(decision)
    if note := _value_period_note(compiled, as_of):
        decision.explanation += " " + note
    return _outcome_from(decision, rule, compiled)


async def lookup_address(
    session: AsyncSession,
    address: Address,
    as_of: dt.date,
    *,
    persist: bool = False,
    include_not_applicable: bool = False,
) -> LookupResponse:
    evidence = evidence_for(address)
    records, compiled, relations = await compiled_rules(session)
    decisions = decide_for_address(records, compiled, relations, evidence, as_of)

    by_id = {r.team_rule_id: r for r in records}
    # Report anything that bears on this address. A rule that definitely does
    # not cover it, and a measure that failed, are dropped; pending and
    # not-yet-effective rules are kept so the answer can say so out loud.
    outcomes = [
        _outcome_from(d, by_id[d.team_rule_id], compiled[d.team_rule_id])
        for d in decisions
        if d.base.could_be_relevant and d.result is not BaseResult.does_not_apply
    ]

    # What is persisted, exported and counted is always the list above - the
    # five results the submission format has words for. `does_not_apply` is
    # appended only for a caller that asked, and never written: it is the
    # absence of a result, not one of them.
    if persist:
        await _persist_lookups(session, address.address_id, as_of, outcomes)

    reportable = outcomes
    if include_not_applicable:
        # A housing provider's question - which exemptions does this building
        # claim - is answerable only from the rules that do *not* bind it, and
        # the exemption check that beat each one is in the trace. That is the
        # one caller this exists for.
        outcomes = [
            *outcomes,
            *(
                _outcome_from(d, by_id[d.team_rule_id], compiled[d.team_rule_id])
                for d in decisions
                if d.result is BaseResult.does_not_apply
            ),
        ]

    juris = address.jurisdiction
    return LookupResponse(
        address_id=address.address_id,
        as_of=as_of,
        legal_city=(evidence.legal_city.value if evidence.legal_city.usable else None),
        legal_state=(evidence.legal_state.value if evidence.legal_state.usable else None),
        resolution_method=(juris.method if juris else None),
        outcomes=outcomes,
        applies_count=sum(1 for o in reportable if o.result == "applies"),
        unknown_count=sum(1 for o in reportable if o.result == "unknown"),
    )


async def _persist_lookups(
    session: AsyncSession, address_id: str, as_of: dt.date, outcomes: list[RuleOutcome]
) -> None:
    existing = (
        (
            await session.execute(
                select(Lookup).where(Lookup.address_id == address_id, Lookup.as_of == as_of)
            )
        )
        .scalars()
        .all()
    )
    by_rule = {row.team_rule_id: row for row in existing}

    for outcome in outcomes:
        row = by_rule.get(outcome.team_rule_id)
        if row is None:
            row = Lookup(
                address_id=address_id,
                team_rule_id=outcome.team_rule_id,
                as_of=as_of,
            )
            session.add(row)
        row.result = outcome.result
        row.explanation = outcome.explanation
        row.conflict_flag = outcome.conflict_flag
        row.unresolved_fields = outcome.unresolved_fields

    # Drop rows this run no longer reports. Without this the table only ever
    # grows: a rule that used to reach an address and now definitely does not
    # would keep its old verdict here forever, and the latest-result view would
    # be a mixture of two runs. The export does not read these rows for exactly
    # that reason, but a stale cache is still worth not keeping.
    reported = {o.team_rule_id for o in outcomes}
    for rule_id, row in by_rule.items():
        if rule_id not in reported:
            await session.delete(row)
    await session.flush()


# ----------------------------------------------------------- submission run ---
# -------------------------------------------------------------- portfolio ---
#: Roll-ups already computed, keyed by what went into them.
#:
#: Evaluating five hundred buildings against a hundred and fifteen rules is
#: ten seconds of arithmetic that produces the same answer every time until a
#: rule changes or a building is added - so the second render of a page is not
#: worth paying for. In-process because this backend is a persistent container
#: rather than a function; a deploy empties it, which is correct, because a
#: deploy is also how the rules change.
_PORTFOLIO_CACHE: dict[tuple, tuple[float, Portfolio]] = {}
PORTFOLIO_TTL_SECONDS = 300
#: How many buildings to name on a rule that misses, or on a missing fact.
#: Enough to go and look at one; not a second copy of the portfolio.
NAMED = 8


def clear_portfolio_cache() -> None:
    _PORTFOLIO_CACHE.clear()


async def portfolio(session: AsyncSession, addresses: list[Address], as_of: dt.date) -> Portfolio:
    """Everything a portfolio page shows, counted here instead of in the browser.

    The caller gets counts and ids. The evidence behind any one of them -
    the checks, the explanation, the quoted span - is a `GET /lookup/{id}`
    away, which is where a reader checking a single answer should be anyway.
    See `schemas.Portfolio` for why this exists at all.
    """
    key = (as_of, tuple(sorted(a.address_id for a in addresses)))
    cached = _PORTFOLIO_CACHE.get(key)
    if cached and monotonic() - cached[0] < PORTFOLIO_TTL_SECONDS:
        return cached[1]

    buildings: list[PortfolioBuilding] = []
    categories: set[str] = set()
    blocked_answers: Counter[str] = Counter()
    blocked_where: dict[str, list[str]] = {}
    misses: dict[str, dict] = {}
    untranslated: set[str] = set()
    not_binding_total = 0

    for address in addresses:
        answer = await lookup_address(
            session, address, as_of, persist=False, include_not_applicable=True
        )
        row = PortfolioBuilding(address_id=address.address_id)
        blocked: set[str] = set()

        for outcome in answer.outcomes:
            category = outcome.category or "uncategorised"
            # `checks` is carried as plain dicts on the wire, so read it as one.
            if outcome.in_jurisdiction and any(
                check.get("reason") == "rule_clause_unmapped" for check in outcome.checks
            ):
                untranslated.add(outcome.team_rule_id)

            if outcome.result == "applies":
                row.applies += 1
                categories.add(category)
                row.by_category.setdefault(
                    category, CategoryCell(binding=0, unsettled=0)
                ).binding += 1
            elif outcome.result == "unknown":
                row.unknown += 1
                categories.add(category)
                row.by_category.setdefault(
                    category, CategoryCell(binding=0, unsettled=0)
                ).unsettled += 1
                for field in outcome.unresolved_fields:
                    blocked.add(field)
                    blocked_answers[field] += 1
                    blocked_where.setdefault(field, []).append(address.address_id)
            elif outcome.result == "does_not_apply" and outcome.in_jurisdiction:
                # Out-of-jurisdiction misses are dropped: "a Berkeley ordinance
                # does not cover your Boston building" is a map, not compliance
                # information, and there are eighty of them per address.
                row.not_binding += 1
                not_binding_total += 1
                exemption = next(
                    (
                        c
                        for c in outcome.checks
                        if c.get("check") == "exemption" and c.get("value") == "true"
                    ),
                    None,
                )
                miss = misses.setdefault(
                    outcome.team_rule_id,
                    {
                        "rule": outcome.team_rule_id,
                        "title": outcome.title,
                        "jurisdiction": outcome.jurisdiction,
                        "category": outcome.category,
                        "citation": outcome.citation,
                        "buildings": 0,
                        "address_ids": [],
                        "why": (exemption.get("detail") if exemption else None)
                        or outcome.explanation,
                        "exemption": exemption is not None,
                    },
                )
                miss["buildings"] += 1
                if len(miss["address_ids"]) < NAMED:
                    miss["address_ids"].append(address.address_id)

        row.blocked_by = sorted(blocked)
        buildings.append(row)

    rolled = [PortfolioRule(**miss) for miss in misses.values()]
    rolled.sort(key=lambda r: (-r.buildings, r.rule))

    result = Portfolio(
        as_of=as_of,
        totals=PortfolioTotals(
            buildings=len(addresses),
            evaluated=sum(1 for b in buildings if b.evaluated),
            fully_answered=sum(1 for b in buildings if b.evaluated and not b.blocked_by),
            not_binding=not_binding_total,
            untranslated_rules=len(untranslated),
        ),
        categories=sorted(categories),
        buildings=buildings,
        # Ordered by how many answers one field would settle, because that is
        # the only ordering that says what to go and find first.
        blocking=[
            BlockingFact(
                field=field,
                answers=answers,
                buildings=len(set(blocked_where[field])),
                address_ids=sorted(set(blocked_where[field]))[:NAMED],
            )
            for field, answers in blocked_answers.most_common()
        ],
        exemptions=[r for r in rolled if r.exemption],
        missed=[r for r in rolled if not r.exemption],
    )
    _PORTFOLIO_CACHE[key] = (monotonic(), result)
    return result


async def run_lookup_export(
    session: AsyncSession,
    as_of: dt.date,
    *,
    write_audit: bool = True,
) -> dict:
    """Evaluate every supplied address once and build lookups.json from it.

    Built from one completed run rather than from whatever `lookups` rows
    happen to carry today's date. Persisted rows are a convenient latest-result
    view and they are overwritten in place, which makes them a cache and not an
    audit trail - so an export assembled from them can silently mix two runs or
    omit an address nobody re-evaluated.

    The audit log is the replayable record: one JSON line per reported
    decision, carrying the rule version it was decided against and every check
    that produced it.
    """
    import json

    from app.modules.change_tracking.validation import require_sample_address_ids

    addresses = (
        (
            await session.execute(
                # The submission file is the sample. An address somebody typed
                # in for themselves is their own data and belongs in no export.
                select(Address)
                .where(Address.imported)
                .options(selectinload(Address.jurisdiction))
                .order_by(Address.address_id)
            )
        )
        .scalars()
        .all()
    )
    try:
        expected_ids = require_sample_address_ids(
            settings.addresses_csv, [address.address_id for address in addresses]
        )
    except ValueError as exc:
        raise SubmissionInvalid(str(exc)) from exc
    records, compiled, relations = await compiled_rules(session)

    decisions: list[Decision] = []
    for address in addresses:
        decisions.extend(
            decide_for_address(records, compiled, relations, evidence_for(address), as_of)
        )

    payload = to_submission(decisions, sorted(expected_ids), as_of)
    problems = validate_submission(
        payload,
        expected_address_ids=sorted(expected_ids),
        known_rule_ids={r.team_rule_id for r in records},
    )

    if write_audit:
        run_id = f"{as_of.isoformat()}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
        path = settings.data_root / "data" / "lookup_runs" / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "run_id": run_id,
                        "as_of": as_of.isoformat(),
                        "compiler_version": COMPILER_VERSION,
                        "rules": {rid: compiled[rid].rule_version_hash for rid in sorted(compiled)},
                        "addresses": len(addresses),
                        "problems": problems,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            for decision in decisions:
                if decision.result in (BaseResult.does_not_apply, BaseResult.failed):
                    continue
                handle.write(json.dumps(decision.to_json(), sort_keys=True) + "\n")

    payload["validation_problems"] = problems
    return payload
