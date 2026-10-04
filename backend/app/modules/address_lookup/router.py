"""Module B routes - addresses, jurisdiction resolution, rule lookup, export."""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import get_session
from app.db.models import Address, AddressJurisdiction
from app.modules.address_lookup import service
from app.modules.address_lookup.adapters.property_facts import from_db_address, to_payload
from app.modules.address_lookup.rule_adapter import adapters as rule_adapters
from app.modules.address_lookup.schemas import (
    AddressDetail,
    AddressRecord,
    AddressStats,
    JurisdictionRecord,
    LookupBatchRequest,
    LookupResponse,
    ResolveRequest,
    ResolveSummary,
    ZipReviewCase,
    ZipReviewRecord,
)
from app.modules.address_lookup.status import jurisdiction_status
from app.modules.address_lookup.status import zip_discrepancy as zip_discrepancy_of
from app.modules.address_lookup.zip_reviews import same_input

router = APIRouter(prefix="/address-lookup", tags=["Module B - address lookup"])


def _default_as_of() -> dt.date:
    return dt.date.fromisoformat(settings.default_as_of)


def _with_juris(stmt):
    return stmt.options(selectinload(Address.jurisdiction))


def _latest_zip_review(address: Address) -> ZipReviewRecord | None:
    if not address.zip_reviews:
        return None
    latest = max(address.zip_reviews, key=lambda row: row.id)
    record = ZipReviewRecord.model_validate(latest)
    return record.model_copy(update={"current": same_input(latest, service.input_from_db(address))})


def _address_record(address: Address) -> AddressRecord:
    jurisdiction = address.jurisdiction
    resolved = jurisdiction_status(jurisdiction) == "resolved"
    legal_city = jurisdiction.legal_city if resolved and jurisdiction else None
    return AddressRecord.model_validate(address).model_copy(
        update={
            "legal_city": legal_city,
            "legal_state": jurisdiction.legal_state if resolved and jurisdiction else None,
            "county": jurisdiction.county if resolved and jurisdiction else None,
            "jurisdiction_status": jurisdiction_status(jurisdiction),
            "zip_discrepancy": zip_discrepancy_of(jurisdiction),
            "postal_city_differs": bool(
                legal_city and legal_city.casefold() != address.postal_city.casefold()
            ),
            # The geocoder's own point, never a stand-in. See AddressRecord.
            "latitude": jurisdiction.latitude if jurisdiction else None,
            "longitude": jurisdiction.longitude if jurisdiction else None,
        }
    )


# ------------------------------------------------------------- addresses ---
@router.get("/addresses", response_model=list[AddressRecord])
async def list_addresses(
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(None, min_length=2, description="Free text over street and city."),
    state: str | None = None,
    postal_city: str | None = None,
    legal_city: str | None = Query(
        None,
        description=(
            "The verified legal city, which is what rules attach to - not the mailing "
            "city. Matched exactly, since this comes from a facet rather than a search box."
        ),
    ),
    resolved: bool | None = Query(None, description="Filter on jurisdiction resolution."),
    zip_discrepancy: bool | None = Query(
        None, description="Only rows whose supplied ZIP disagrees with the geocoder."
    ),
    missing_year_built: bool | None = None,
    missing_units: bool | None = None,
    built_from: int | None = Query(None, ge=1700, le=2100, description="Year built, inclusive."),
    built_to: int | None = Query(None, ge=1700, le=2100, description="Year built, inclusive."),
    units_from: int | None = Query(None, ge=0, description="Unit count, inclusive."),
    units_to: int | None = Query(None, ge=0, description="Unit count, inclusive."),
    mappable: bool | None = Query(
        None, description="Only rows that have a coordinate, or only those that do not."
    ),
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> list[AddressRecord]:
    stmt = _with_juris(select(Address).order_by(Address.address_id))
    if q:
        # What somebody types looking for their own building: part of the street, or the
        # city, or both. Street and city are one field to the person searching, so they are
        # one filter here rather than two they would have to split by hand.
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Address.street_address.ilike(needle), Address.postal_city.ilike(needle))
        )
    if state:
        stmt = stmt.where(Address.state == state.upper())
    if postal_city:
        stmt = stmt.where(Address.postal_city.ilike(f"%{postal_city}%"))
    if legal_city:
        # Only a verified resolution counts. Without the method check this would quietly
        # match a city that is still under review, which is the one mistake this whole
        # module exists to avoid.
        stmt = stmt.join(AddressJurisdiction).where(
            func.lower(AddressJurisdiction.legal_city) == legal_city.strip().lower(),
            AddressJurisdiction.method.in_(service.VERIFIED_METHODS),
        )
    if built_from is not None:
        stmt = stmt.where(Address.year_built >= built_from)
    if built_to is not None:
        stmt = stmt.where(Address.year_built <= built_to)
    if units_from is not None:
        stmt = stmt.where(Address.units >= units_from)
    if units_to is not None:
        stmt = stmt.where(Address.units <= units_to)
    if missing_year_built is True:
        stmt = stmt.where(Address.year_built.is_(None))
    elif missing_year_built is False:
        stmt = stmt.where(Address.year_built.isnot(None))
    if missing_units is True:
        stmt = stmt.where(Address.units.is_(None))
    elif missing_units is False:
        stmt = stmt.where(Address.units.isnot(None))
    if resolved is True:
        stmt = stmt.join(AddressJurisdiction).where(
            AddressJurisdiction.method.in_(service.VERIFIED_METHODS)
        )
    elif resolved is False:
        stmt = stmt.outerjoin(AddressJurisdiction).where(
            or_(
                AddressJurisdiction.address_id.is_(None),
                AddressJurisdiction.method.notin_(service.VERIFIED_METHODS),
            )
        )
    if mappable is True:
        stmt = stmt.join(AddressJurisdiction).where(
            AddressJurisdiction.latitude.isnot(None), AddressJurisdiction.longitude.isnot(None)
        )
    elif mappable is False:
        stmt = stmt.outerjoin(AddressJurisdiction).where(
            or_(
                AddressJurisdiction.address_id.is_(None),
                AddressJurisdiction.latitude.is_(None),
                AddressJurisdiction.longitude.is_(None),
            )
        )

    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().unique().all()
    if zip_discrepancy is not None:
        # Derived from the stored assessment rather than a column, so it is filtered after
        # the query. The set is at most the sample, so this costs nothing worth a schema
        # change - and a ZIP finding deliberately does not live in the jurisdiction columns.
        rows = [a for a in rows if zip_discrepancy_of(a.jurisdiction) is zip_discrepancy]
    return [_address_record(a) for a in rows]


@router.get("/addresses/{address_id}", response_model=AddressDetail)
async def get_address(
    address_id: str, session: AsyncSession = Depends(get_session)
) -> AddressDetail:
    stmt = _with_juris(select(Address).where(Address.address_id == address_id)).options(
        selectinload(Address.zip_reviews)
    )
    address = (await session.execute(stmt)).scalar_one_or_none()
    if address is None:
        raise HTTPException(404, f"No address {address_id}")
    juris = address.jurisdiction
    return AddressDetail(
        **_address_record(address).model_dump(),
        jurisdiction=(JurisdictionRecord.model_validate(juris) if juris else None),
        property_facts=address.property_facts or to_payload(from_db_address(address)),
        zip_review=_latest_zip_review(address),
        postal_city_differs=bool(
            juris
            and juris.legal_city
            and juris.legal_city.casefold() != address.postal_city.casefold()
        ),
    )


@router.get("/zip-review", response_model=list[ZipReviewCase])
async def list_zip_review_cases(
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
) -> list[ZipReviewCase]:
    """Show automatic ZIP discrepancies with any source-backed human finding."""
    stmt = _with_juris(select(Address).order_by(Address.address_id)).options(
        selectinload(Address.zip_reviews)
    )
    addresses = (await session.execute(stmt)).scalars().all()
    cases: list[ZipReviewCase] = []
    for address in addresses:
        juris = address.jurisdiction
        assessment = juris.zip_assessment if juris else None
        if not assessment or assessment.get("status") not in {
            "invalid_for_state",
            "mismatch",
            "matches_some_endpoints",
        }:
            continue
        if status and assessment["status"] != status:
            continue
        cases.append(
            ZipReviewCase(
                address_id=address.address_id,
                street_address=address.street_address,
                postal_city=address.postal_city,
                state=address.state,
                legal_city=juris.legal_city,
                assessment=assessment,
                review=_latest_zip_review(address),
            )
        )
    return cases


# ------------------------------------------------------- jurisdiction fix ---
@router.post("/resolve", response_model=ResolveSummary)
async def resolve_jurisdictions(
    payload: ResolveRequest,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, le=500, description="Cap per call; Census is public."),
) -> ResolveSummary:
    """Resolve mailing city -> legal city via the Census geocoder."""
    stmt = _with_juris(select(Address).order_by(Address.address_id))
    if payload.address_ids:
        stmt = stmt.where(Address.address_id.in_(payload.address_ids))
    elif not payload.force:
        stmt = stmt.outerjoin(AddressJurisdiction).where(
            or_(
                AddressJurisdiction.address_id.is_(None),
                AddressJurisdiction.method.notin_(service.VERIFIED_METHODS),
            )
        )
    addresses = (await session.execute(stmt.limit(limit))).scalars().all()
    if not addresses:
        return ResolveSummary(requested=0, resolved=0, by_method={}, city_corrections=0)

    rows = await service.resolve_many(session, list(addresses))

    by_method: dict[str, int] = {}
    corrections = 0
    postal = {a.address_id: a.postal_city for a in addresses}
    for row in rows:
        by_method[row.method] = by_method.get(row.method, 0) + 1
        if (
            row.method in service.VERIFIED_METHODS
            and row.legal_city
            and row.legal_city.casefold() != postal[row.address_id].casefold()
        ):
            corrections += 1

    return ResolveSummary(
        requested=len(addresses),
        resolved=sum(row.method in service.VERIFIED_METHODS for row in rows),
        by_method=by_method,
        city_corrections=corrections,
    )


@router.post("/addresses/{address_id}/resolve", response_model=JurisdictionRecord)
async def resolve_single(
    address_id: str, session: AsyncSession = Depends(get_session)
) -> JurisdictionRecord:
    stmt = _with_juris(select(Address).where(Address.address_id == address_id))
    address = (await session.execute(stmt)).scalar_one_or_none()
    if address is None:
        raise HTTPException(404, f"No address {address_id}")
    rows = await service.resolve_many(session, [address])
    return JurisdictionRecord.model_validate(rows[0])


@router.get("/jurisdictions", response_model=list[JurisdictionRecord])
async def list_jurisdictions(
    session: AsyncSession = Depends(get_session),
    method: str | None = None,
    limit: int = Query(200, le=500),
) -> list[JurisdictionRecord]:
    stmt = select(AddressJurisdiction).order_by(AddressJurisdiction.address_id)
    if method:
        stmt = stmt.where(AddressJurisdiction.method == method)
    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    return [JurisdictionRecord.model_validate(r) for r in rows]


# ---------------------------------------------------------------- lookup ---
@router.get("/lookup/export")
async def export_lookups(
    session: AsyncSession = Depends(get_session),
    as_of: dt.date | None = None,
    validate: bool = Query(True, description="Refuse to serve a file that fails validation."),
) -> Response:
    """submission_templates/lookups.json, built from one complete run.

    Evaluated fresh for every supplied address rather than read back from the
    `lookups` table: those rows are overwritten in place, so a file assembled
    from them can mix two runs or quietly omit an address nobody re-evaluated.
    """
    day = as_of or _default_as_of()
    payload = await service.run_lookup_export(session, day)
    problems = payload.pop("validation_problems", [])

    if problems and validate:
        raise HTTPException(
            500,
            {
                "detail": "The export failed validation and was not served.",
                "problems": problems[:20],
                "problem_count": len(problems),
            },
        )

    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="lookups.json"'},
    )


@router.get("/rule-compilation")
async def rule_compilation(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """What the rule adapter made of Module A's prose, and what needs a human.

    This is the review surface for Module B: a rule with untranslated text
    answers `unknown` for every address it could reach, so the queue is ordered
    by how much it is costing.
    """
    records, compiled, relations = await service.compiled_rules(session)
    store = rule_adapters.store()
    return {
        "summary": rule_adapters.unreviewed_summary(store),
        "queue": store.queue()[:50],
        "rules": [
            {
                "team_rule_id": rule_id,
                "jurisdiction": rule.jurisdiction,
                "issue_key": rule.issue_key,
                "review_state": str(rule.review_state),
                "coverage": rule.coverage.to_json(),
                "exemptions": rule.exemptions.to_json(),
                "effective_dates": [d.to_json() for d in rule.effective_dates],
                "unmapped_text": [u.to_json() for u in rule.unmapped_text],
                "notes": list(rule.notes),
            }
            for rule_id, rule in sorted(compiled.items())
        ],
        "relations": [r.to_json() for r in relations],
    }


@router.post("/lookup", response_model=list[LookupResponse])
async def lookup_batch(
    payload: LookupBatchRequest, session: AsyncSession = Depends(get_session)
) -> list[LookupResponse]:
    """Evaluate several addresses in one request.

    One call rather than one per address, because the rule set is compiled once
    for the batch - which is what makes a housing provider's whole portfolio
    affordable to render on a single page.
    """
    day = payload.as_of or _default_as_of()
    stmt = _with_juris(select(Address).order_by(Address.address_id))
    if payload.address_ids:
        stmt = stmt.where(Address.address_id.in_(payload.address_ids))
    addresses = (await session.execute(stmt.limit(payload.limit))).scalars().all()
    return [
        await service.lookup_address(
            session,
            a,
            day,
            persist=payload.persist,
            include_not_applicable=payload.include_not_applicable,
        )
        for a in addresses
    ]


@router.get("/lookup/{address_id}", response_model=LookupResponse)
async def lookup_one(
    address_id: str,
    session: AsyncSession = Depends(get_session),
    as_of: dt.date | None = Query(None, description=f"Defaults to {settings.default_as_of}."),
    persist: bool = False,
    include_not_applicable: bool = Query(
        False,
        description=(
            "Also return rules that definitely do not cover this address, so the caller "
            "can see which exemption beat each one. Never persisted, never exported, and "
            "not counted in applies_count or unknown_count."
        ),
    ),
) -> LookupResponse:
    stmt = _with_juris(select(Address).where(Address.address_id == address_id))
    address = (await session.execute(stmt)).scalar_one_or_none()
    if address is None:
        raise HTTPException(404, f"No address {address_id}")
    return await service.lookup_address(
        session,
        address,
        as_of or _default_as_of(),
        persist=persist,
        include_not_applicable=include_not_applicable,
    )


@router.get("/stats", response_model=AddressStats)
async def address_stats(session: AsyncSession = Depends(get_session)) -> AddressStats:
    async def group(column) -> dict[str, int]:
        rows = await session.execute(select(column, func.count()).group_by(column))
        return {str(k): n for k, n in rows}

    total = (await session.execute(select(func.count()).select_from(Address))).scalar_one()
    resolved = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .where(AddressJurisdiction.method.in_(service.VERIFIED_METHODS))
        )
    ).scalar_one()
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
    corrections = (
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

    legal_cities = {
        str(city): n
        for city, n in await session.execute(
            select(AddressJurisdiction.legal_city, func.count())
            .where(
                AddressJurisdiction.legal_city.isnot(None),
                AddressJurisdiction.method.in_(service.VERIFIED_METHODS),
            )
            .group_by(AddressJurisdiction.legal_city)
        )
    }
    mappable = (
        await session.execute(
            select(func.count())
            .select_from(AddressJurisdiction)
            .where(
                AddressJurisdiction.latitude.isnot(None),
                AddressJurisdiction.longitude.isnot(None),
            )
        )
    ).scalar_one()

    return AddressStats(
        total=total,
        by_state=await group(Address.state),
        by_postal_city=await group(Address.postal_city),
        by_legal_city=legal_cities,
        resolved=resolved,
        unresolved=total - resolved,
        by_method=await group(AddressJurisdiction.method),
        city_corrections=corrections,
        missing_year_built=no_year,
        missing_units=no_units,
        with_coordinates=mappable,
    )
