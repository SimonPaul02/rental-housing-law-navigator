"""Module B: resolve a jurisdiction, then decide which rules apply."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import asdict
from threading import Lock

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
from app.modules.address_lookup.rule_evaluation.export import to_submission, validate_submission
from app.modules.address_lookup.rule_evaluation.interactions import resolve_interactions
from app.modules.address_lookup.schemas import LookupResponse, RuleOutcome
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
    key = f"{record.team_rule_id}@{version}"
    with _COMPILED_LOCK:
        hit = _COMPILED.get(key)
    if hit is not None:
        return hit

    store = rule_adapters.store()
    compiled = store.get(record.team_rule_id, version)
    if compiled is None:
        from app.modules.address_lookup.rule_adapter import compiler

        compiled, _ = compiler.compile_rule(record)
    compiled = store.apply_reviews(compiled)
    with _COMPILED_LOCK:
        _COMPILED[key] = compiled
    return compiled


def clear_compiled_cache() -> None:
    with _COMPILED_LOCK:
        _COMPILED.clear()


async def compiled_rules(
    session: AsyncSession,
) -> tuple[list[Rule], dict[str, CompiledRule], list[Relation]]:
    """Every rule, its compiled form, and the reviewed relations between them."""
    rows = await session.execute(select(Rule).order_by(Rule.team_rule_id))
    records = list(rows.scalars().all())
    store = rule_adapters.store()
    compiled = {r.team_rule_id: compiled_for(r) for r in records}
    return records, compiled, store.all_relations()


def _outcome_from(decision: Decision, record: Rule) -> RuleOutcome:
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
        key_value=record.key_value,
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
        row = address.jurisdiction or AddressJurisdiction(address_id=result.address_id)
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
    return _outcome_from(decision, rule)


async def lookup_address(
    session: AsyncSession,
    address: Address,
    as_of: dt.date,
    *,
    persist: bool = False,
) -> LookupResponse:
    evidence = evidence_for(address)
    records, compiled, relations = await compiled_rules(session)
    decisions = decide_for_address(records, compiled, relations, evidence, as_of)

    by_id = {r.team_rule_id: r for r in records}
    # Report anything that bears on this address. A rule that definitely does
    # not cover it, and a measure that failed, are dropped; pending and
    # not-yet-effective rules are kept so the answer can say so out loud.
    outcomes = [
        _outcome_from(d, by_id[d.team_rule_id])
        for d in decisions
        if d.base.could_be_relevant and d.result is not BaseResult.does_not_apply
    ]

    if persist:
        await _persist_lookups(session, address.address_id, as_of, outcomes)

    juris = address.jurisdiction
    return LookupResponse(
        address_id=address.address_id,
        as_of=as_of,
        legal_city=(evidence.legal_city.value if evidence.legal_city.usable else None),
        legal_state=(evidence.legal_state.value if evidence.legal_state.usable else None),
        resolution_method=(juris.method if juris else None),
        outcomes=outcomes,
        applies_count=sum(1 for o in outcomes if o.result == "applies"),
        unknown_count=sum(1 for o in outcomes if o.result == "unknown"),
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
    await session.flush()


# ----------------------------------------------------------- submission run ---
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

    records, compiled, relations = await compiled_rules(session)
    addresses = (
        (
            await session.execute(
                select(Address)
                .options(selectinload(Address.jurisdiction))
                .order_by(Address.address_id)
            )
        )
        .scalars()
        .all()
    )

    decisions: list[Decision] = []
    for address in addresses:
        decisions.extend(
            decide_for_address(records, compiled, relations, evidence_for(address), as_of)
        )

    payload = to_submission(decisions, [a.address_id for a in addresses], as_of)
    problems = validate_submission(
        payload,
        expected_address_ids=[a.address_id for a in addresses],
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
