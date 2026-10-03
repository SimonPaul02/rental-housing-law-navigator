"""Module B routes - addresses, jurisdiction resolution, rule lookup, export."""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import get_session
from app.db.models import Address, AddressJurisdiction, Lookup
from app.modules.address_lookup import service
from app.modules.address_lookup.schemas import (
    AddressDetail,
    AddressRecord,
    AddressStats,
    JurisdictionRecord,
    LookupBatchRequest,
    LookupResponse,
    ResolveRequest,
    ResolveSummary,
)

router = APIRouter(prefix="/address-lookup", tags=["Module B - address lookup"])


def _default_as_of() -> dt.date:
    return dt.date.fromisoformat(settings.default_as_of)


def _with_juris(stmt):
    return stmt.options(selectinload(Address.jurisdiction))


# ------------------------------------------------------------- addresses ---
@router.get("/addresses", response_model=list[AddressRecord])
async def list_addresses(
    session: AsyncSession = Depends(get_session),
    state: str | None = None,
    postal_city: str | None = None,
    resolved: bool | None = Query(None, description="Filter on jurisdiction resolution."),
    missing_year_built: bool | None = None,
    missing_units: bool | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> list[AddressRecord]:
    stmt = select(Address).order_by(Address.address_id)
    if state:
        stmt = stmt.where(Address.state == state.upper())
    if postal_city:
        stmt = stmt.where(Address.postal_city.ilike(f"%{postal_city}%"))
    if missing_year_built is True:
        stmt = stmt.where(Address.year_built.is_(None))
    elif missing_year_built is False:
        stmt = stmt.where(Address.year_built.isnot(None))
    if missing_units is True:
        stmt = stmt.where(Address.units.is_(None))
    elif missing_units is False:
        stmt = stmt.where(Address.units.isnot(None))
    if resolved is True:
        stmt = stmt.join(AddressJurisdiction)
    elif resolved is False:
        stmt = stmt.outerjoin(AddressJurisdiction).where(AddressJurisdiction.address_id.is_(None))

    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [AddressRecord.model_validate(a) for a in rows]


@router.get("/addresses/{address_id}", response_model=AddressDetail)
async def get_address(
    address_id: str, session: AsyncSession = Depends(get_session)
) -> AddressDetail:
    stmt = _with_juris(select(Address).where(Address.address_id == address_id))
    address = (await session.execute(stmt)).scalar_one_or_none()
    if address is None:
        raise HTTPException(404, f"No address {address_id}")
    juris = address.jurisdiction
    return AddressDetail(
        **AddressRecord.model_validate(address).model_dump(),
        jurisdiction=(JurisdictionRecord.model_validate(juris) if juris else None),
        postal_city_differs=bool(
            juris
            and juris.legal_city
            and juris.legal_city.casefold() != address.postal_city.casefold()
        ),
    )


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
        stmt = stmt.outerjoin(AddressJurisdiction).where(AddressJurisdiction.address_id.is_(None))
    addresses = (await session.execute(stmt.limit(limit))).scalars().all()
    if not addresses:
        return ResolveSummary(requested=0, resolved=0, by_method={}, city_corrections=0)

    rows = await service.resolve_many(session, list(addresses))

    by_method: dict[str, int] = {}
    corrections = 0
    postal = {a.address_id: a.postal_city for a in addresses}
    for row in rows:
        by_method[row.method] = by_method.get(row.method, 0) + 1
        if row.legal_city and row.legal_city.casefold() != postal[row.address_id].casefold():
            corrections += 1

    return ResolveSummary(
        requested=len(addresses),
        resolved=len(rows),
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
) -> Response:
    """submission_templates/lookups.json shape."""
    day = as_of or _default_as_of()
    rows = (
        (
            await session.execute(
                select(Lookup)
                .where(Lookup.as_of == day)
                .order_by(Lookup.address_id, Lookup.team_rule_id)
            )
        )
        .scalars()
        .all()
    )
    lookups: dict[str, list[dict]] = {}
    for row in rows:
        lookups.setdefault(row.address_id, []).append(
            {
                "team_rule_id": row.team_rule_id,
                "result": row.result,
                "explanation": row.explanation,
                "conflict_flag": row.conflict_flag,
            }
        )
    body = {"as_of": day.isoformat(), "lookups": lookups}
    return Response(
        content=json.dumps(body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="lookups.json"'},
    )


@router.post("/lookup", response_model=list[LookupResponse])
async def lookup_batch(
    payload: LookupBatchRequest, session: AsyncSession = Depends(get_session)
) -> list[LookupResponse]:
    day = payload.as_of or _default_as_of()
    stmt = _with_juris(select(Address).order_by(Address.address_id))
    if payload.address_ids:
        stmt = stmt.where(Address.address_id.in_(payload.address_ids))
    addresses = (await session.execute(stmt.limit(payload.limit))).scalars().all()
    return [
        await service.lookup_address(session, a, day, persist=payload.persist) for a in addresses
    ]


@router.get("/lookup/{address_id}", response_model=LookupResponse)
async def lookup_one(
    address_id: str,
    session: AsyncSession = Depends(get_session),
    as_of: dt.date | None = Query(None, description=f"Defaults to {settings.default_as_of}."),
    persist: bool = False,
) -> LookupResponse:
    stmt = _with_juris(select(Address).where(Address.address_id == address_id))
    address = (await session.execute(stmt)).scalar_one_or_none()
    if address is None:
        raise HTTPException(404, f"No address {address_id}")
    return await service.lookup_address(
        session, address, as_of or _default_as_of(), persist=persist
    )


@router.get("/stats", response_model=AddressStats)
async def address_stats(session: AsyncSession = Depends(get_session)) -> AddressStats:
    async def group(column) -> dict[str, int]:
        rows = await session.execute(select(column, func.count()).group_by(column))
        return {str(k): n for k, n in rows}

    total = (await session.execute(select(func.count()).select_from(Address))).scalar_one()
    resolved = (
        await session.execute(select(func.count()).select_from(AddressJurisdiction))
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

    return AddressStats(
        total=total,
        by_state=await group(Address.state),
        by_postal_city=await group(Address.postal_city),
        resolved=resolved,
        unresolved=total - resolved,
        by_method=await group(AddressJurisdiction.method),
        city_corrections=corrections,
        missing_year_built=no_year,
        missing_units=no_units,
    )
