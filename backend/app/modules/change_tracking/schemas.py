"""Module C payloads."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class CanonicalMatch(BaseModel):
    """How a challenge rule id (e.g. 'CA-ALG-01') maps onto our extracted rules."""

    canonical_id: str
    selector: str = Field(description="Human-readable description of the selector.")
    matched_rule_ids: list[str] = Field(default_factory=list)
    matched: bool = False
    note: str | None = None
    sources: list[dict[str, str | None]] = Field(default_factory=list)


class ChangeTest(BaseModel):
    test_id: str
    title: str
    type: str
    rule_ids: list[str]
    expected_behavior: str
    as_of: dt.date | None = None
    as_of_before: dt.date | None = None
    as_of_after: dt.date | None = None
    states: list[str] = Field(default_factory=list)
    conflict_with: list[str] = Field(default_factory=list)


class ChangeTestResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    test_id: str
    title: str
    type: str
    as_of: dt.date
    affected_address_ids: list[str]
    conflict_flag_address_ids: list[str] = Field(default_factory=list)
    notes: str
    expected_behavior: str
    canonical_matches: list[CanonicalMatch] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)
    # True when every canonical id in the test resolved to a real rule.
    rules_resolved: bool = True
    # Why this case could not be replayed at all, when it could not be.
    #
    # Set only by `service.blocked_result`, and it changes how every other
    # field here must be read: the affected set is empty because nothing was
    # computed, not because the answer is nobody. Anything counting addresses
    # has to skip these rows rather than add their zeroes in.
    blocked_reason: str | None = None


class RunTestsRequest(BaseModel):
    test_ids: list[str] | None = Field(default=None, description="Omit to run all.")
    persist: bool = True
    address_limit: int = Field(default=500, le=500, description="Cap addresses scanned per test.")
