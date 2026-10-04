"""Request and response models for the accounts module."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Who the person is to rental housing law.

    These four are the whole taxonomy, and each one gets its own interface and its own
    features. They are not a hierarchy - an agency is not a renter with more buttons - so
    nothing here orders or nests them.
    """

    renter = "renter"
    provider = "provider"
    agency = "agency"
    advocate = "advocate"


# The label is the one the interface shows; it lives next to the enum so the frontend can
# read it from /api/meta instead of keeping a second copy that drifts.
ROLE_LABELS: dict[Role, str] = {
    Role.renter: "Renter",
    Role.provider: "Housing provider",
    Role.agency: "Housing agency",
    Role.advocate: "Housing advocate",
}


class Profile(BaseModel):
    """The WorkOS user, as the frontend's server read it out of the sealed session.

    It never arrives from the browser. `/auth/register` in the Next.js app builds this from
    `withAuth()`, so the email here is the one WorkOS issued the session for.
    """

    email: str = Field(max_length=320)
    name: str | None = Field(default=None, max_length=200)
    picture_url: str | None = Field(default=None, max_length=2000)


class RegisterRequest(Profile):
    role: Role


class RoleChange(BaseModel):
    role: Role


class Account(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workos_user_id: str
    role: Role
    role_label: str
    email: str
    name: str | None
    picture_url: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime | None


class PlaceRequest(BaseModel):
    address_id: str = Field(max_length=16)
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class TypedPlaceRequest(BaseModel):
    """A building that is not in the address book this account was set up with.

    Free text rather than fields, because that is how somebody has it: pasted
    from a letter, a lease or a map. `street, city, state` is what the resolver
    needs, and the error says so when it is not there.
    """

    address: str = Field(
        min_length=6,
        max_length=300,
        description="street, city, state - e.g. '415 Mission St, San Francisco, CA'.",
    )
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class PlaceUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class Place(BaseModel):
    """A saved building, with enough of the address to render a card without a second call."""

    id: int
    address_id: str
    label: str | None
    note: str | None
    street_address: str
    postal_city: str
    state: str
    zip: str | None
    year_built: int | None
    units: int | None
    legal_city: str | None
    legal_state: str | None
    jurisdiction_status: str
    zip_discrepancy: bool
    # True when the mailing city is not the legal one - the single most consequential thing
    # about an address in this data, so it is on the card rather than a click away.
    postal_city_differs: bool
    # Where to draw it. Present only once the jurisdiction has been resolved, because the
    # coordinate comes from the same geocoder answer the legal city does - an address with
    # no verified jurisdiction has no verified point either, and guessing one would put a
    # pin somewhere nobody checked.
    latitude: float | None = None
    longitude: float | None = None
    contract_count: int = 0
    created_at: dt.datetime


# ------------------------------------------------------------------- contracts ---
#: The hard cap on an upload. A lease is a few hundred kilobytes; this is generous
#: enough for a scanned one and small enough that the bytes can live in the row.
MAX_CONTRACT_BYTES = 12 * 1024 * 1024

#: What may be uploaded. An allowlist rather than a denylist, because the file is served
#: back from the same origin the app runs on: anything that a browser might execute there
#: must be impossible to store, not merely discouraged. No HTML, no SVG, no scripts.
CONTRACT_TYPES: dict[str, str] = {
    "application/pdf": "PDF",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/heic": "HEIC",
    "image/webp": "WebP",
    "text/plain": "text",
    "application/msword": "Word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
}


class ContractMeta(BaseModel):
    """Everything about an agreement except the file itself.

    The rent and the term are self-reported and nothing here verifies them. They exist so
    a figure can be shown next to the rule that governs it; no entitlement is ever computed
    from them, because that would be advice and this is not that.
    """

    unit_label: str | None = Field(
        default=None,
        max_length=64,
        description="Which unit the agreement covers. Omit for a whole building.",
    )
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    monthly_rent_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    note: str | None = Field(default=None, max_length=2000)


class Contract(ContractMeta):
    id: int
    place_id: int
    filename: str
    content_type: str
    kind: str = Field(description="The content type as a person would name it, e.g. PDF.")
    byte_size: int
    sha256: str
    page_count: int | None = None
    created_at: dt.datetime
