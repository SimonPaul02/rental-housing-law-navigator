"""Module B: resolve a jurisdiction, then decide which rules apply."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import asdict
from threading import Lock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Address, AddressJurisdiction, Lookup, Rule
from app.modules.address_lookup import coverage
from app.modules.address_lookup.adapters.address_files import read_review_overrides_csv
from app.modules.address_lookup.adapters.property_facts import from_db_address
from app.modules.address_lookup.address_resolution.census import CachedGeocoder, CensusGeocoder
from app.modules.address_lookup.address_resolution.models import AddressInput, ResolvedAddress
from app.modules.address_lookup.address_resolution.resolver import (
    apply_review_overrides,
    resolve_addresses,
)
from app.modules.address_lookup.schemas import LookupResponse, RuleOutcome

log = logging.getLogger(__name__)
_GEOCODE_LOCK = Lock()
VERIFIED_METHODS = frozenset({"geocoder", "review_override", "census", "manual"})


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


def parse_effective_date(value: str | None) -> dt.date | None:
    """Accepts YYYY, YYYY-MM or YYYY-MM-DD; returns the earliest covered day."""
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return dt.date(year, month, day)
    except (ValueError, IndexError):
        log.warning("unparseable effective_date %r", value)
        return None


def facts_for(address: Address) -> coverage.AddressFacts:
    juris = address.jurisdiction
    property_record = from_db_address(address)
    return coverage.AddressFacts(
        address_id=address.address_id,
        legal_city=(juris.legal_city if juris and juris.method in VERIFIED_METHODS else None),
        legal_state=(juris.legal_state or address.state) if juris else address.state,
        year_built=(
            property_record.year_built.value
            if property_record.year_built.status == "present"
            else None
        ),
        units=(property_record.units.value if property_record.units.status == "present" else None),
        use_code=(
            property_record.use_code.value if property_record.use_code.status == "present" else None
        ),
        certificate_of_occupancy_date=(
            property_record.certificate_of_occupancy_date.value
            if property_record.certificate_of_occupancy_date.status == "present"
            else None
        ),
    )


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
    rule: Rule, facts: coverage.AddressFacts, as_of: dt.date
) -> RuleOutcome:
    """The full decision for one (rule, address, date) triple."""
    context = {
        "team_rule_id": rule.team_rule_id,
        "category": rule.category,
        "jurisdiction": rule.jurisdiction,
        "level": rule.level,
        "status": rule.status,
        "title": rule.title,
        "key_value": rule.key_value,
        "citation": rule.citation,
        "source_url": rule.source_url,
        "quoted_span": rule.quoted_span,
    }

    if rule.level == "city" and rule.status != "failed" and facts.legal_city is None:
        city_state = rule.jurisdiction.rpartition(",")[2].strip().upper()
        if not city_state or city_state == (facts.legal_state or "").upper():
            return RuleOutcome(
                result="unknown",
                explanation="The legal city has not been verified for this address.",
                unresolved_fields=["legal_city"],
                **context,
            )

    # 1. Geography.
    geo_ok, geo_reason = coverage.jurisdiction_matches(
        rule_jurisdiction=rule.jurisdiction, rule_level=rule.level, facts=facts
    )
    if not geo_ok:
        return RuleOutcome(
            result="does_not_apply",
            explanation=geo_reason,
            in_jurisdiction=False,
            **context,
        )

    # 2. Status and effective date as of the query date.
    if rule.status == "failed":
        return RuleOutcome(
            result="does_not_apply",
            explanation=(
                f"{rule.citation} was defeated or struck, so it imposes no "
                "requirement. Recorded as failed."
            ),
            **context,
        )
    if rule.status == "pending":
        return RuleOutcome(
            result="does_not_apply",
            explanation=(
                f"{rule.citation} is still pending and not in force on "
                f"{as_of.isoformat()}. Reported as pending, not applied."
            ),
            **context,
        )

    effective = parse_effective_date(rule.effective_date)
    if effective and effective > as_of:
        return RuleOutcome(
            result="does_not_apply",
            explanation=(
                f"Enacted but not yet effective: takes effect "
                f"{rule.effective_date}, after the query date {as_of.isoformat()}."
            ),
            **context,
        )
    if rule.status == "not_yet_effective" and not effective:
        return RuleOutcome(
            result="unknown",
            explanation=(
                "Recorded as not yet effective but the source gives no "
                "effective date, so its status on "
                f"{as_of.isoformat()} cannot be determined."
            ),
            unresolved_fields=["effective_date"],
            **context,
        )

    # 3. Coverage conditions and exemptions. These pull in opposite
    #    directions, so they are evaluated separately: a coverage condition
    #    must hold for the rule to bite, an exemption must NOT hold.
    cover_preds = coverage.parse_conditions(rule.coverage_conditions)
    exempt_preds = coverage.parse_conditions(rule.exemptions)

    cover = coverage.evaluate(cover_preds, facts)
    exempt = coverage.evaluate_exemption(exempt_preds, facts)

    if not cover_preds and not exempt_preds:
        return RuleOutcome(
            result="applies",
            explanation=(
                f"{geo_reason} In force on {as_of.isoformat()} with no coverage "
                "condition this data could narrow."
            ),
            conflict_flag=rule.conflict_flag,
            **context,
        )

    reasons = [*cover.reasons, *exempt.reasons]
    unresolved = sorted(set(cover.unresolved) | set(exempt.unresolved))

    if cover.outcome is coverage.Outcome.fails:
        result = "does_not_apply"
    elif exempt.outcome is coverage.ExemptionOutcome.exempt:
        result = "does_not_apply"
        reasons.append("The building falls inside the rule's exemption.")
    elif (
        cover.outcome is coverage.Outcome.unknown
        or exempt.outcome is coverage.ExemptionOutcome.unknown
    ):
        result = "unknown"
    else:
        result = "applies"
        if exempt_preds:
            reasons.append("The exemption cannot apply to this building, so the rule stands.")

    return RuleOutcome(
        result=result,
        explanation=" ".join(reasons) or geo_reason,
        conflict_flag=rule.conflict_flag,
        unresolved_fields=unresolved if result == "unknown" else [],
        **context,
    )


async def lookup_address(
    session: AsyncSession,
    address: Address,
    as_of: dt.date,
    *,
    persist: bool = False,
) -> LookupResponse:
    facts = facts_for(address)
    rules = (await session.execute(select(Rule).order_by(Rule.team_rule_id))).scalars().all()

    outcomes = [evaluate_rule_for_address(r, facts, as_of) for r in rules]
    # Report rules that bear on the address. Rules that simply belong to
    # another jurisdiction are dropped; anything not in force is kept so the
    # answer can say "pending" or "not yet effective" out loud.
    relevant = [
        o
        for o in outcomes
        if o.in_jurisdiction
        and (
            o.result in {"applies", "unknown"}
            or o.status in {"pending", "not_yet_effective", "failed"}
        )
    ]

    if persist:
        await _persist_lookups(session, address.address_id, as_of, relevant)

    juris = address.jurisdiction
    return LookupResponse(
        address_id=address.address_id,
        as_of=as_of,
        legal_city=facts.legal_city,
        legal_state=facts.legal_state,
        resolution_method=(juris.method if juris else None),
        outcomes=relevant,
        applies_count=sum(1 for o in relevant if o.result == "applies"),
        unknown_count=sum(1 for o in relevant if o.result == "unknown"),
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
