"""Storage-independent inputs and outputs for address resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AddressInput:
    address_id: str
    street_address: str
    postal_city: str
    state: str
    zip: str = ""
    year_built: str = ""
    units: str = ""
    use_code: str = ""
    use_description: str = ""
    source_dataset: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class AddressQuery:
    street: str
    city: str
    state: str
    zip: str = ""


@dataclass(frozen=True)
class GeocodeCandidate:
    matched_address: str
    street_address: str
    state: str
    county: str | None
    city: str | None
    city_geoid: str | None
    longitude: float
    latitude: float
    benchmark: str
    vintage: str


@dataclass(frozen=True)
class GeocodeResponse:
    candidates: tuple[GeocodeCandidate, ...]
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LookupAttempt:
    query: AddressQuery
    outcome: str  # match, no_match, rejected, ambiguous, or error
    detail: str = ""


@dataclass(frozen=True)
class ResolvedAddress:
    address_id: str
    input: AddressInput
    status: str  # resolved or needs_review
    legal_state: str | None
    legal_county: str | None
    legal_city: str | None
    city_geoid: str | None
    longitude: float | None
    latitude: float | None
    matched_address: str | None
    benchmark: str | None
    vintage: str | None
    warnings: tuple[str, ...] = ()
    attempts: tuple[LookupAttempt, ...] = ()
    candidates: tuple[GeocodeCandidate, ...] = ()
