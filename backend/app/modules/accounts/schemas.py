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
    created_at: dt.datetime
