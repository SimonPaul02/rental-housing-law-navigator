"""/api/accounts - the signed-in person's own account and their own saved buildings.

Every route here depends on `require_principal`, so there is no path through this module
that does not start from a signature-verified WorkOS token. The path never carries a user
id; the token is the only thing that says who is asking.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_principal
from app.core.db import get_session

from . import service
from .schemas import (
    Account,
    Place,
    PlaceRequest,
    PlaceUpdate,
    RegisterRequest,
    RoleChange,
)

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
