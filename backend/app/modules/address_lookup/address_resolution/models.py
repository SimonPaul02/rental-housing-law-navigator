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
    zip: str | None = None


@dataclass(frozen=True)
class GeocodeResponse:
    candidates: tuple[GeocodeCandidate, ...]
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AcceptedEndpoint:
    input_street: str
    candidate: GeocodeCandidate
    match_kind: str  # exact or normalized


@dataclass(frozen=True)
class ZipAssessment:
    # missing_input, invalid_for_state, insufficient_evidence, matches_all,
    # matches_some_endpoints, or mismatch
    status: str
    input_zip: str
    matched_zips: tuple[str, ...]
    expected_endpoints: int
    accepted_endpoints: int
    reason: str
    source_dataset: str = ""
    source_retrieved_at: str = ""


@dataclass(frozen=True)
class LookupAttempt:
    query: AddressQuery
    # match, normalized_match, no_match, rejected, ambiguous, invalid_input,
    # cache_miss, or service_error
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class ReviewOverride:
    address_id: str
    input_street_address: str
    input_postal_city: str
    input_state: str
    legal_state: str
    legal_city: str
    source_url: str
    reason: str
    reviewer: str
    reviewed_at: str
    legal_county: str | None = None
    city_geoid: str | None = None


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
    resolution_method: str = "unresolved"  # geocoder, review_override, or unresolved
    review_override: ReviewOverride | None = None
    zip_assessment: ZipAssessment | None = None
    accepted_endpoints: tuple[AcceptedEndpoint, ...] = ()
    warnings: tuple[str, ...] = ()
    attempts: tuple[LookupAttempt, ...] = ()
    candidates: tuple[GeocodeCandidate, ...] = ()
