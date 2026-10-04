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
from app.modules.address_lookup.rule_evaluation.confidence import assess, assess_all
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult, Decision, Ternary
from app.modules.address_lookup.rule_evaluation.derived_facts import derive
from app.modules.address_lookup.rule_evaluation.explanations import explain
from app.modules.address_lookup.rule_evaluation.export import (
    SubmissionInvalid,
    to_submission,
    validate_submission,
)
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

    evidence = pred.AddressEvidence(
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
        use_description=fact("use_description"),
        jurisdiction_method=(juris.method if juris else None),
        jurisdiction_note=(juris.note if juris else None),
    )
    return derive(evidence, (address.state or "").upper())


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


def _carry_record_conflict(decision: Decision, record: Rule) -> None:
    """Module A's own conflict flag belongs to every answer about the rule.

    Folded into the decision rather than the outcome, so the API view, the
    audit log and lookups.json all report the same flag.
    """
    if record.conflict_flag and not decision.conflict_flag:
        decision.conflict_flag = True
        decision.conflict_reason = decision.conflict_reason or (
            f"The sources disagree about this rule: {record.conflict_note}."
            if record.conflict_note
            else "The sources disagree about this rule."
        )


def _outcome_from(decision: Decision, record: Rule, compiled: CompiledRule) -> RuleOutcome:
    return RuleOutcome(
        team_rule_id=decision.team_rule_id,
        result=str(decision.result),
        explanation=decision.explanation,
        conflict_flag=decision.conflict_flag,
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
        confidence=str(decision.confidence),
        confidence_reasons=list(decision.confidence_reasons),
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
    by_id = {r.team_rule_id: r for r in records}
    for decision in decisions:
        _carry_record_conflict(decision, by_id[decision.team_rule_id])
    assess_all(decisions, compiled)
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
    _carry_record_conflict(decision, rule)
    assess(decision, compiled)
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
    reportable, outcomes = _reported_outcomes(
        decisions, records, compiled, include_not_applicable=include_not_applicable
    )

    # What is persisted, exported and counted is always `reportable` - the
    # five results the submission format has words for.
    if persist:
        await _persist_lookups(session, address.address_id, as_of, reportable)

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


def _reported_outcomes(
    decisions: list[Decision],
    records: list[Rule],
    compiled: dict[str, CompiledRule],
    *,
    include_not_applicable: bool = False,
) -> tuple[list[RuleOutcome], list[RuleOutcome]]:
    """The reportable outcomes, and the list the caller asked to see.

    A rule that definitely does not cover the address, and a measure that
    failed, are dropped; pending and not-yet-effective rules are kept so the
    answer can say so out loud. `does_not_apply` is appended only for a caller
    that asked, and never persisted: it is the absence of a result. A housing
    provider's question - which exemptions does this building claim - is
    answerable only from the rules that do *not* bind it, and the exemption
    check that beat each one is in the trace.
    """
    by_id = {r.team_rule_id: r for r in records}

    def outcome(d: Decision) -> RuleOutcome:
        return _outcome_from(d, by_id[d.team_rule_id], compiled[d.team_rule_id])

    reportable = [
        outcome(d)
        for d in decisions
        if d.base.could_be_relevant and d.result is not BaseResult.does_not_apply
    ]
    if not include_not_applicable:
        return reportable, reportable
    return reportable, [
        *reportable,
        *(outcome(d) for d in decisions if d.result is BaseResult.does_not_apply),
    ]


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
                select(Address)
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
