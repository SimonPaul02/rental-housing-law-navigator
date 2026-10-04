"""Module B payloads."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class AddressRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address_id: str
    street_address: str
    postal_city: str
    state: str
    zip: str | None = None
    year_built: int | None = None
    units: int | None = None
    use_code: str | None = None
    use_description: str | None = None
    legal_city: str | None = None
    legal_state: str | None = None
    county: str | None = None
    jurisdiction_status: str = "not_checked"
    zip_discrepancy: bool = False
    postal_city_differs: bool = False
    # Where to draw it, when there is anywhere. This is the geocoder's own point and
    # nothing is substituted for it: an unresolved address has none, and so does one a
    # human resolved by override. A map therefore places fewer rows than exist, which is a
    # fact about the records worth showing rather than papering over.
    latitude: float | None = None
    longitude: float | None = None


class ZipAssessmentRecord(BaseModel):
    status: str
    input_zip: str
    matched_zips: list[str]
    expected_endpoints: int
    accepted_endpoints: int
    reason: str
    source_dataset: str = ""
    source_retrieved_at: str = ""


class ZipReviewRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    address_id: str
    input_street_address: str
    input_postal_city: str
    input_state: str
    input_zip: str
    decision: str
    confirmed_zip: str | None = None
    source_url: str
    reason: str
    reviewer: str
    reviewed_at: dt.date
    current: bool = False


class JurisdictionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address_id: str
    legal_city: str | None = None
    legal_state: str | None = None
    county: str | None = None
    method: str
    matched_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_geoid: str | None = None
    confidence: float | None = None
    note: str | None = None
    resolution_evidence: dict | None = None
    zip_assessment: ZipAssessmentRecord | None = None


class AddressDetail(AddressRecord):
    jurisdiction: JurisdictionRecord | None = None
    property_facts: dict | None = None
    zip_review: ZipReviewRecord | None = None
    postal_city_differs: bool = Field(
        default=False,
        description="True when the legal city is not the mailing city.",
    )


class ZipReviewCase(BaseModel):
    address_id: str
    street_address: str
    postal_city: str
    state: str
    legal_city: str | None = None
    assessment: ZipAssessmentRecord
    review: ZipReviewRecord | None = None


class RuleOutcome(BaseModel):
    team_rule_id: str
    result: str = Field(
        description=(
            "applies | unknown | superseded | not_yet_effective | pending, plus the "
            "internal-only does_not_apply. The submission file carries the five; "
            "does_not_apply exists so the UI and the audit trail can say that a rule was "
            "considered and definitely does not cover this address."
        )
    )
    explanation: str
    conflict_flag: bool = False
    unresolved_fields: list[str] = Field(default_factory=list)
    in_jurisdiction: bool = Field(
        default=True,
        description="False when the rule belongs to another state or city entirely.",
    )
    # Context so the UI can render without a second request.
    category: str | None = None
    jurisdiction: str | None = None
    level: str | None = None
    status: str | None = None
    title: str | None = None
    key_value: str | None = None
    citation: str | None = None
    source_url: str | None = None
    quoted_span: str | None = None
    # Why this decision, in the evaluator's own terms. The challenge export has
    # only four fields, so the trace stays in the API view.
    superseded_by: str | None = None
    confidence: str = Field(
        default="high",
        description=(
            "high when every check behind the answer is the cited source or a named "
            "reviewer; low when one rests on the machine's own reading. The reasons "
            "say which."
        ),
    )
    confidence_reasons: list[str] = Field(default_factory=list)
    issue_key: str | None = None
    checks: list[dict] = Field(default_factory=list)


class LookupResponse(BaseModel):
    address_id: str
    as_of: dt.date
    legal_city: str | None = None
    legal_state: str | None = None
    resolution_method: str | None = None
    outcomes: list[RuleOutcome]
    applies_count: int
    unknown_count: int


class ResolveRequest(BaseModel):
    address_ids: list[str] | None = Field(
        default=None, description="Omit to resolve every unresolved address."
    )
    force: bool = Field(default=False, description="Re-resolve already-resolved rows.")


class ResolveSummary(BaseModel):
    requested: int
    resolved: int
    by_method: dict[str, int]
    city_corrections: int = Field(
        description="Addresses whose legal city differs from the mailing city."
    )


class LookupBatchRequest(BaseModel):
    address_ids: list[str] | None = None
    as_of: dt.date | None = None
    persist: bool = True
    limit: int = Field(default=500, le=500)
    include_not_applicable: bool = Field(
        default=False,
        description=(
            "Also return rules that definitely do not cover each address. Never persisted "
            "and never counted - see the same flag on GET /lookup/{address_id}."
        ),
    )


class AddressStats(BaseModel):
    total: int
    by_state: dict[str, int]
    by_postal_city: dict[str, int]
    # The legal city, which is the one rules attach to - and therefore the one a filter
    # should offer. Counted only over verified resolutions, so a mailing city can never
    # appear here as though it had been confirmed.
    by_legal_city: dict[str, int]
    resolved: int
    unresolved: int
    by_method: dict[str, int]
    city_corrections: int
    missing_year_built: int
    missing_units: int
    # How many rows a map can actually place. Lower than `resolved`, because a human
    # override settles the legal city without producing a coordinate.
    with_coordinates: int = 0
