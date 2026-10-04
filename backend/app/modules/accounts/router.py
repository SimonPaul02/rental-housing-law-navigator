"""/api/accounts - the signed-in person's own account and their own saved buildings.

Every route here depends on `require_principal`, so there is no path through this module
that does not start from a signature-verified WorkOS token. The path never carries a user
id; the token is the only thing that says who is asking.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_principal
from app.core.db import get_session

from . import service
from .schemas import (
    CONTRACT_TYPES,
    MAX_CONTRACT_BYTES,
    Account,
    Contract,
    ContractMeta,
    Place,
    PlaceRequest,
    PlaceUpdate,
    RegisterRequest,
    RoleChange,
    TypedPlaceRequest,
)

#: Which uploads a browser may render in place rather than download. PDFs and images
#: only, and never because the upload said so - the type was checked against an
#: allowlist on the way in, and `nosniff` stops the browser second-guessing it. Nothing
#: a browser could execute on this origin is storable in the first place.
INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/me", response_model=Account | None)
async def me(
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Account | None:
    """The caller's account, or `null` if they have signed in but not yet chosen a role.

    `null` with a 200 rather than a 404: not having picked a role yet is a step in signing
    up, not a failed request, and the frontend branches on it rather than on an error.
    """
    return await service.read(session, principal)


@router.put("/me", response_model=Account)
async def register(
    payload: RegisterRequest,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Account:
    """Create the account on first sign-in, or refresh its cached profile on later ones.

    The profile in the body comes from the Next.js server, which reads it out of the sealed
    AuthKit session - not from the browser. The id it is stored under is the token's `sub`
    regardless of what the body says, so the worst a tampered body could do is mislabel the
    caller's own row.
    """
    return await service.register(
        session,
        principal,
        role=payload.role,
        email=payload.email,
        name=payload.name,
        picture_url=payload.picture_url,
    )


@router.patch("/me", response_model=Account)
async def change_role(
    payload: RoleChange,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Account:
    """Switch role.

    Self-service, because there is no administrator in this deployment to ask: with no
    organisations there is nobody above an account. A role decides which interface and which
    features a person gets, not what data they may reach - every row this API serves is
    either public corpus material or the caller's own - so switching it grants nothing.
    """
    return await service.set_role(session, principal, payload.role)


@router.delete("/me", status_code=204)
async def forget_me(
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete our record of the caller, saved places included.

    Their WorkOS account survives - it is not ours to delete - so signing in again starts
    over at the role picker.
    """
    await service.forget(session, principal)
    return Response(status_code=204)


# ------------------------------------------------------------- saved places ---
@router.get("/me/places", response_model=list[Place])
async def list_places(
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> list[Place]:
    return await service.places(session, principal)


@router.post("/me/places", response_model=Place, status_code=201)
async def add_place(
    payload: PlaceRequest,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Place:
    """Save a building. Adding one already saved returns it rather than failing."""
    return await service.add_place(
        session,
        principal,
        address_id=payload.address_id,
        label=payload.label,
        note=payload.note,
    )


@router.post("/me/places/by-address", response_model=Place, status_code=201)
async def add_typed_place(
    payload: TypedPlaceRequest,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Place:
    """Save a building that is not in the address book this account came with.

    The import carries a year built and a unit count for every row; a typed
    address has neither, and none is invented - the rules that turn on them
    answer "unknown" and name the field. What it does get is the legal
    jurisdiction, from the same resolver the import ran.

    Open to any signed-in caller, because a role is not a permission here and
    every row this writes is the caller's own. Which roles are *offered* it is
    a different question and is decided in the interface: an agency holds no
    addresses of its own, and a provider works from the book they imported.
    """
    return await service.add_typed_place(
        session,
        principal,
        address=payload.address,
        label=payload.label,
        note=payload.note,
    )


@router.patch("/me/places/{place_id}", response_model=Place)
async def update_place(
    place_id: int,
    payload: PlaceUpdate,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Place:
    return await service.update_place(
        session, principal, place_id, label=payload.label, note=payload.note
    )


@router.delete("/me/places/{place_id}", status_code=204)
async def remove_place(
    place_id: int,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await service.remove_place(session, principal, place_id)
    return Response(status_code=204)


# ------------------------------------------------------- tenancy agreements ---
# A lease is the most private thing in this database, and these five routes are the only
# way in or out of it. Each one starts from `require_principal` and filters on the token's
# `sub`; a row belonging to somebody else is reported as missing rather than forbidden,
# because "not yours" would confirm it exists.
#
# Why they hang off a place rather than standing alone: an agreement is only meaningful
# against a building, and the building is what says which rules the figures in it should be
# read beside. A contract with no address would be a file in a drawer.
@router.get("/me/contracts", response_model=list[Contract])
async def list_all_contracts(
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> list[Contract]:
    """Every agreement the caller has uploaded, across all of their buildings."""
    return await service.contracts(session, principal)


@router.get("/me/places/{place_id}/contracts", response_model=list[Contract])
async def list_contracts(
    place_id: int,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> list[Contract]:
    return await service.contracts(session, principal, place_id)


@router.post("/me/places/{place_id}/contracts", response_model=Contract, status_code=201)
async def upload_contract(
    place_id: int,
    file: UploadFile = File(description=f"Max {MAX_CONTRACT_BYTES // (1024 * 1024)} MB."),
    unit_label: str | None = Form(default=None, max_length=64),
    starts_on: dt.date | None = Form(default=None),
    ends_on: dt.date | None = Form(default=None),
    monthly_rent_cents: int | None = Form(default=None, ge=0, le=100_000_000),
    note: str | None = Form(default=None, max_length=2000),
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Contract:
    """File a tenancy agreement against one of the caller's own buildings.

    Multipart rather than JSON because the file is the point, and the details around it
    arrive in the same submission as the one the person filled in.

    One building holds many agreements, told apart by `unit_label`: a renter has one lease
    and no unit to name, a thirty-two unit building has thirty-two. Re-uploading the same
    bytes for the same unit returns the existing row instead of filing a second copy.
    """
    # Read before validating the type, because the declared type is the only thing that
    # can be trusted here and an empty body is the more common mistake.
    data = await file.read()
    if len(data) > MAX_CONTRACT_BYTES:
        raise HTTPException(
            413,
            f"That file is {len(data) // (1024 * 1024)} MB. The limit is "
            f"{MAX_CONTRACT_BYTES // (1024 * 1024)} MB.",
        )
    return await service.add_contract(
        session,
        principal,
        place_id,
        filename=file.filename or "agreement",
        content_type=(file.content_type or "").split(";")[0].strip().lower(),
        data=data,
        meta=ContractMeta(
            unit_label=(unit_label or None),
            starts_on=starts_on,
            ends_on=ends_on,
            monthly_rent_cents=monthly_rent_cents,
            note=(note or None),
        ),
    )


@router.patch("/me/contracts/{contract_id}", response_model=Contract)
async def edit_contract(
    contract_id: int,
    payload: ContractMeta,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Contract:
    """Correct the unit, the term, the rent or the note. The file is never edited."""
    return await service.update_contract(session, principal, contract_id, payload)


@router.get("/me/contracts/{contract_id}/file")
async def download_contract(
    contract_id: int,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Hand the caller back their own file.

    `nosniff` and an explicit disposition, because this is served from the same origin the
    app runs on. Only a PDF or an image is shown in place; anything else downloads. The
    filename is percent-encoded into `filename*` so a lease called `Mietvertrag Köln.pdf`
    arrives with its name intact and cannot smuggle a quote into the header.
    """
    row = await service.one_contract(session, principal, contract_id)
    disposition = "inline" if row.content_type in INLINE_TYPES else "attachment"
    return Response(
        content=row.data,
        media_type=row.content_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''{quote(row.filename, safe='')}"
            ),
            "X-Content-Type-Options": "nosniff",
            # Nobody else may hold this, not even a shared cache for a moment.
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/me/contracts/{contract_id}", status_code=204)
async def delete_contract(
    contract_id: int,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete an agreement. The bytes are in the row, so this is the whole deletion."""
    await service.remove_contract(session, principal, contract_id)
    return Response(status_code=204)


@router.get("/me/contract-types")
async def contract_types() -> dict:
    """What may be uploaded, so the file picker and the error message agree."""
    return {
        "max_bytes": MAX_CONTRACT_BYTES,
        "accept": sorted(CONTRACT_TYPES),
        "names": sorted(set(CONTRACT_TYPES.values())),
    }
