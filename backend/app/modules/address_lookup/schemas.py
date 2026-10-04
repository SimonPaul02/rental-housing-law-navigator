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


# --------------------------------------------------------------- portfolio ---
# A roll-up over a set of buildings, computed here rather than in the browser.
#
# The page it feeds used to fetch every outcome for every building and do this
# arithmetic itself, which is fine for the nine buildings somebody types in and
# impossible for the five hundred an account is set up with: 57,500 outcomes,
# each carrying its checks, its explanation and its quoted span, is 75 MB of
# JSON and a page that never renders. Stripping the audit trail only reaches
# 17 MB, so the answer is not a thinner outcome - it is to send the roll-up
# instead of the thing it was rolled up from.
#
# What that costs is the drill-down: these shapes carry counts and ids, not
# evidence. A building's full record is one `GET /lookup/{address_id}` away,
# and that is the right place for it - a reader checking one answer wants every
# check behind it, and a reader looking at a portfolio wants none of them.


class CategoryCell(BaseModel):
    """One cell of the building x obligation matrix."""

    binding: int
    unsettled: int


class PortfolioBuilding(BaseModel):
    address_id: str
    #: False when the address could not be evaluated at all.
    evaluated: bool = True
    applies: int = 0
    unknown: int = 0
    #: In-jurisdiction rules tested against this building that do not reach it.
    not_binding: int = 0
    #: The fields that would settle this building's unknowns, named.
    blocked_by: list[str] = Field(default_factory=list)
    #: Only the categories with something in them, so a wide matrix stays small.
    by_category: dict[str, CategoryCell] = Field(default_factory=dict)


class PortfolioRule(BaseModel):
    """A rule that misses, counted over the buildings it was tested against.

    Aggregated by rule rather than listed per building, and that is a better
    answer rather than a cheaper one: "this rent cap was tested against 312 of
    your buildings and misses every one of them on the unit count" is the
    compliance fact. Thirty-five thousand rows saying it one building at a time
    is the same fact, unreadable.
    """

    rule: str
    title: str | None = None
    jurisdiction: str | None = None
    category: str | None = None
    citation: str | None = None
    #: How many buildings it was tested against and missed.
    buildings: int
    #: The first few, so a reader can go and look at one.
    address_ids: list[str] = Field(default_factory=list)
    #: Why it missed, in the evaluator's own words. The same for every building
    #: it missed on unless they differ, in which case this is one of them.
    why: str | None = None
    #: True when an exemption is what let the buildings off, rather than the
    #: rule simply not reaching them. The two are different compliance facts.
    exemption: bool = False


class BlockingFact(BaseModel):
    field: str
    #: How many answers supplying this one field would settle.
    answers: int
    buildings: int
    address_ids: list[str] = Field(default_factory=list)


class PortfolioTotals(BaseModel):
    buildings: int
    evaluated: int
    #: Buildings with no answer blocked by a missing fact.
    fully_answered: int
    #: In-jurisdiction rules tested and missed, across the portfolio.
    not_binding: int
    #: Rules reaching these buildings that still carry prose no condition was
    #: made from. Such a rule cannot be tested, so neither its coverage nor its
    #: exemptions can be ruled out.
    untranslated_rules: int


class Portfolio(BaseModel):
    as_of: dt.date
    totals: PortfolioTotals
    #: In a stable order, so the matrix's columns do not move between renders.
    categories: list[str]
    buildings: list[PortfolioBuilding]
    blocking: list[BlockingFact]
    #: Rules an exemption let these buildings off.
    exemptions: list[PortfolioRule]
    #: Rules that missed on a building fact rather than an exemption.
    missed: list[PortfolioRule]
