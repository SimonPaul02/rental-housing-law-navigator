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
import hashlib
import io
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.core.auth import Principal
from app.db.models import Address, PlaceContract, SavedPlace, User
from app.modules.address_lookup.status import jurisdiction_status, zip_discrepancy

from .schemas import (
    CONTRACT_TYPES,
    MAX_CONTRACT_BYTES,
    ROLE_LABELS,
    Account,
    Contract,
    ContractMeta,
    Place,
    Role,
)

log = logging.getLogger(__name__)


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
        legal_city=juris.legal_city if jurisdiction_status(juris) == "resolved" else None,
        legal_state=juris.legal_state if jurisdiction_status(juris) == "resolved" else None,
        jurisdiction_status=jurisdiction_status(juris),
        zip_discrepancy=zip_discrepancy(juris),
        postal_city_differs=bool(
            juris
            and jurisdiction_status(juris) == "resolved"
            and juris.legal_city
            and juris.legal_city.casefold() != address.postal_city.casefold()
        ),
        # Whatever the geocoder actually returned, and nothing more. An unresolved row has
        # no coordinate, and so does one a human resolved by override - so a map can place
        # fewer buildings than a person has saved, and has to say so rather than invent a
        # point for the rest.
        latitude=juris.latitude if juris else None,
        longitude=juris.longitude if juris else None,
        contract_count=len(row.contracts),
        created_at=row.created_at,
    )


def _owned(owner_id: str):
    return (
        select(SavedPlace)
        .where(SavedPlace.owner_id == owner_id)
        .options(
            selectinload(SavedPlace.address).selectinload(Address.jurisdiction),
            # Loaded eagerly but without the bytes: a list of buildings needs to say how
            # many agreements each one carries, not to carry them.
            selectinload(SavedPlace.contracts).options(defer(PlaceContract.data)),
        )
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


# --------------------------------------------------------------- contracts ---
def _contract(row: PlaceContract) -> Contract:
    return Contract(
        id=row.id,
        place_id=row.place_id,
        unit_label=row.unit_label,
        filename=row.filename,
        content_type=row.content_type,
        kind=CONTRACT_TYPES.get(row.content_type, "file"),
        byte_size=row.byte_size,
        sha256=row.sha256,
        page_count=row.page_count,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        monthly_rent_cents=row.monthly_rent_cents,
        note=row.note,
        created_at=row.created_at,
    )


def _page_count(content_type: str, data: bytes) -> int | None:
    """How many pages a PDF has, or None.

    Worth knowing on the card - a two-page lease and a forty-page one are different
    documents - and cheap, since pypdf is already here for the corpus. A file that will not
    parse is still kept: it is the person's own lease and refusing it over a page count
    would be absurd.
    """
    if content_type != "application/pdf":
        return None
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001 - informational only
        log.info("could not read page count from an uploaded PDF: %s", exc)
        return None


def _contracts_of(owner_id: str, place_id: int | None = None):
    """Every agreement the caller owns, optionally narrowed to one building.

    Filtered on `owner_id` directly rather than through the place, so the owner check does
    not depend on the join being written correctly. Columns are listed explicitly to leave
    `data` behind: a listing never needs the bytes.
    """
    stmt = (
        select(PlaceContract)
        .where(PlaceContract.owner_id == owner_id)
        .order_by(PlaceContract.unit_label, PlaceContract.id)
        .options(defer(PlaceContract.data))
    )
    if place_id is not None:
        stmt = stmt.where(PlaceContract.place_id == place_id)
    return stmt


async def contracts(
    session: AsyncSession, principal: Principal, place_id: int | None = None
) -> list[Contract]:
    if place_id is not None:
        # 404s for a place that is not the caller's, which is also what proves the
        # contracts under it are theirs to list.
        await one_place(session, principal, place_id)
    rows = (await session.execute(_contracts_of(principal.user_id, place_id))).scalars()
    return [_contract(row) for row in rows]


async def one_contract(
    session: AsyncSession, principal: Principal, contract_id: int
) -> PlaceContract:
    """Load an agreement the caller owns, with its bytes, or 404.

    Reported as missing rather than forbidden when it belongs to somebody else: "not yours"
    would confirm the row exists, and this is the one lookup where an account could
    otherwise probe for other people's leases.
    """
    stmt = select(PlaceContract).where(
        PlaceContract.id == contract_id, PlaceContract.owner_id == principal.user_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No such agreement.")
    return row


async def add_contract(
    session: AsyncSession,
    principal: Principal,
    place_id: int,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    meta: ContractMeta,
) -> Contract:
    """Store an agreement against one of the caller's own buildings."""
    place = await one_place(session, principal, place_id)

    if content_type not in CONTRACT_TYPES:
        raise HTTPException(
            415,
            f"{content_type or 'that file type'} cannot be uploaded. "
            f"Accepted: {', '.join(sorted(set(CONTRACT_TYPES.values())))}.",
        )
    if not data:
        raise HTTPException(422, "The file is empty.")
    if len(data) > MAX_CONTRACT_BYTES:
        raise HTTPException(
            413,
            f"That file is {len(data) // (1024 * 1024)} MB. The limit is "
            f"{MAX_CONTRACT_BYTES // (1024 * 1024)} MB.",
        )
    if meta.starts_on and meta.ends_on and meta.ends_on < meta.starts_on:
        raise HTTPException(422, "The agreement cannot end before it starts.")

    digest = hashlib.sha256(data).hexdigest()
    duplicate = (
        await session.execute(
            select(PlaceContract).where(
                PlaceContract.owner_id == principal.user_id,
                PlaceContract.place_id == place.id,
                PlaceContract.sha256 == digest,
                PlaceContract.unit_label == meta.unit_label,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        # The same bytes against the same unit is the same agreement. Returning it keeps
        # a double-click from filing a second copy of somebody's lease.
        return _contract(duplicate)

    row = PlaceContract(
        owner_id=principal.user_id,
        place_id=place.id,
        unit_label=meta.unit_label,
        filename=filename[:255],
        content_type=content_type,
        byte_size=len(data),
        sha256=digest,
        page_count=_page_count(content_type, data),
        starts_on=meta.starts_on,
        ends_on=meta.ends_on,
        monthly_rent_cents=meta.monthly_rent_cents,
        note=meta.note,
        data=data,
    )
    session.add(row)
    await session.flush()
    return _contract(row)


async def update_contract(
    session: AsyncSession, principal: Principal, contract_id: int, meta: ContractMeta
) -> Contract:
    """Correct the details around an agreement. The file itself is never edited."""
    if meta.starts_on and meta.ends_on and meta.ends_on < meta.starts_on:
        raise HTTPException(422, "The agreement cannot end before it starts.")
    row = await one_contract(session, principal, contract_id)
    row.unit_label = meta.unit_label
    row.starts_on = meta.starts_on
    row.ends_on = meta.ends_on
    row.monthly_rent_cents = meta.monthly_rent_cents
    row.note = meta.note
    await session.flush()
    return _contract(row)


async def remove_contract(session: AsyncSession, principal: Principal, contract_id: int) -> None:
    """Delete an agreement. The bytes are in the row, so this is the whole deletion."""
    row = await one_contract(session, principal, contract_id)
    await session.delete(row)
