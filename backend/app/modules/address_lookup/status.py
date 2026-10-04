"""Small presentation statuses derived from stored address evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.db.models import AddressJurisdiction

VERIFIED_METHODS = frozenset({"geocoder", "review_override", "census", "manual"})
JurisdictionStatus = Literal["resolved", "needs_review", "not_checked"]
ZIP_DISCREPANCIES = frozenset({"invalid_for_state", "mismatch", "matches_some_endpoints"})


def jurisdiction_status(jurisdiction: AddressJurisdiction | None) -> JurisdictionStatus:
    if jurisdiction is None:
        return "not_checked"
    return "resolved" if jurisdiction.method in VERIFIED_METHODS else "needs_review"


def zip_discrepancy(jurisdiction: AddressJurisdiction | None) -> bool:
    assessment = jurisdiction.zip_assessment if jurisdiction else None
    return bool(assessment and assessment.get("status") in ZIP_DISCREPANCIES)
