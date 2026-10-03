"""Account and saved-place persistence.

Two rules hold throughout, and they are the reason this module is separate from the three
challenge modules:

1. The only identity ever trusted is `principal.user_id` - the `sub` of a signature-verified
   WorkOS access token. Nothing reads a user id out of a path, a query or a body.
2. Every query is filtered by that id. Not "filtered unless the role says otherwise":
   a housing agency has no more right to another person's saved home than a renter does,
   because these rows are nobody's business but their owner's.
"""

from __future__ import annotations

import datetime as dt

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import Principal
from app.db.models import Address, SavedPlace, User

from .schemas import ROLE_LABELS, Account, Place, Role


def _account(user: User) -> Account:
    role = Role(user.role)
    return Account(
        workos_user_id=user.workos_user_id,
        role=role,
        role_label=ROLE_LABELS[role],
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
    )


async def find(session: AsyncSession, principal: Principal) -> User | None:
    return await session.get(User, principal.user_id)


async def read(session: AsyncSession, principal: Principal) -> Account | None:
    """The account, or None for somebody who has signed in but not yet picked a role.

    None is an ordinary answer, not an error: Google sign-up hands us a verified identity
    and nothing else, and the role is the next question the app asks.
    """
    user = await find(session, principal)
    if user is None:
        return None
    # Cheap enough to write on every read, and it is the only activity signal there is.
    user.last_seen_at = dt.datetime.now(dt.UTC)
    return _account(user)


async def register(
    session: AsyncSession,
    principal: Principal,
    *,
    role: Role,
    email: str,
    name: str | None,
    picture_url: str | None,
) -> Account:
    """Create the account, or refresh the profile of one that already exists.

    Idempotent on purpose - the frontend calls this on every sign-in so a changed name or
    avatar in WorkOS follows along. The role is only set when the row is new: changing it
    later is a deliberate act with its own endpoint, not a side effect of signing in.
    """
    user = await find(session, principal)
    if user is None:
        user = User(workos_user_id=principal.user_id, role=role.value, email=email)
        session.add(user)
    user.email = email
    user.name = name
    user.picture_url = picture_url
    user.last_seen_at = dt.datetime.now(dt.UTC)
    await session.flush()
    return _account(user)


async def set_role(session: AsyncSession, principal: Principal, role: Role) -> Account:
    user = await find(session, principal)
    if user is None:
        raise HTTPException(404, "No account yet. Choose a role first.")
    user.role = role.value
    await session.flush()
    return _account(user)


async def forget(session: AsyncSession, principal: Principal) -> None:
    """Delete our record of a person. Their WorkOS account is not ours to delete."""
    user = await find(session, principal)
    if user is not None:
        await session.delete(user)


# ------------------------------------------------------------- saved places ---
def _place(row: SavedPlace) -> Place:
    address = row.address
    juris = address.jurisdiction
    return Place(
        id=row.id,
        address_id=row.address_id,
        label=row.label,
        note=row.note,
        street_address=address.street_address,
        postal_city=address.postal_city,
        state=address.state,
        zip=address.zip,
        year_built=address.year_built,
        units=address.units,
        legal_city=juris.legal_city if juris else None,
        legal_state=juris.legal_state if juris else None,
        postal_city_differs=bool(
            juris
            and juris.legal_city
            and juris.legal_city.casefold() != address.postal_city.casefold()
        ),
        created_at=row.created_at,
    )


def _owned(owner_id: str):
    return (
        select(SavedPlace)
        .where(SavedPlace.owner_id == owner_id)
        .options(selectinload(SavedPlace.address).selectinload(Address.jurisdiction))
    )


async def places(session: AsyncSession, principal: Principal) -> list[Place]:
    stmt = _owned(principal.user_id).order_by(SavedPlace.created_at)
    return [_place(row) for row in (await session.execute(stmt)).scalars()]


async def one_place(session: AsyncSession, principal: Principal, place_id: int) -> SavedPlace:
    """Load a place the caller owns, or 404.

    A place somebody else owns is reported as missing rather than forbidden - "not yours"
    would confirm that the row exists, and this is the one lookup where an account could
    otherwise probe for other people's data.
    """
    stmt = _owned(principal.user_id).where(SavedPlace.id == place_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No such saved place.")
    return row


async def add_place(
    session: AsyncSession,
    principal: Principal,
    *,
    address_id: str,
    label: str | None,
    note: str | None,
) -> Place:
    user = await find(session, principal)
    if user is None:
        raise HTTPException(404, "No account yet. Choose a role first.")
    address = await session.get(Address, address_id)
    if address is None:
        # The sample is the extent of the building data this prototype has; an address
        # outside it has no year built and no unit count, so every coverage condition that
        # turns on those would answer "unknown" and the card would say nothing.
        raise HTTPException(404, f"No address {address_id} in the sample.")
    existing = (
        await session.execute(_owned(principal.user_id).where(SavedPlace.address_id == address_id))
    ).scalar_one_or_none()
    if existing is not None:
        return _place(existing)
    row = SavedPlace(owner_id=principal.user_id, address_id=address_id, label=label, note=note)
    session.add(row)
    await session.flush()
    # Re-read with the address and its jurisdiction eagerly loaded: the card wants the
    # legal city, and a lazy load on an async session would raise instead of fetching it.
    return _place(await one_place(session, principal, row.id))


async def update_place(
    session: AsyncSession,
    principal: Principal,
    place_id: int,
    *,
    label: str | None,
    note: str | None,
) -> Place:
    row = await one_place(session, principal, place_id)
    row.label = label
    row.note = note
    await session.flush()
    return _place(row)


async def remove_place(session: AsyncSession, principal: Principal, place_id: int) -> None:
    row = await one_place(session, principal, place_id)
    await session.delete(row)
