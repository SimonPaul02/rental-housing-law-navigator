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
    result: str = Field(description="applies | does_not_apply | unknown")
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


class AddressStats(BaseModel):
    total: int
    by_state: dict[str, int]
    by_postal_city: dict[str, int]
    resolved: int
    unresolved: int
    by_method: dict[str, int]
    city_corrections: int
    missing_year_built: int
    missing_units: int
