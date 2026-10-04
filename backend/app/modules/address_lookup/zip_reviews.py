"""Validate source-backed ZIP decisions independently of their storage."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.modules.address_lookup.address_resolution.models import AddressInput, ZipAssessment

DECISIONS = frozenset(
    {"input_zip_supported", "census_zip_supported", "another_zip_supported", "inconclusive"}
)


@dataclass(frozen=True)
class ZipReviewDecision:
    address_id: str
    input_street_address: str
    input_postal_city: str
    input_state: str
    input_zip: str
    decision: str
    confirmed_zip: str | None
    source_url: str
    reason: str
    reviewer: str
    reviewed_at: dt.date


def same_input(decision: ZipReviewDecision, address: AddressInput) -> bool:
    """A reviewed decision applies only to the address version it examined."""

    def normalize(value: str) -> str:
        return " ".join(value.split()).casefold()

    return (
        decision.address_id == address.address_id
        and normalize(decision.input_street_address) == normalize(address.street_address)
        and normalize(decision.input_postal_city) == normalize(address.postal_city)
        and decision.input_state.upper() == address.state.upper()
        and decision.input_zip == address.zip.strip()
    )


def validate_decision(
    decision: ZipReviewDecision, address: AddressInput, assessment: ZipAssessment | None
) -> None:
    if not same_input(decision, address):
        raise ValueError(f"ZIP review has stale address input: {decision.address_id}")
    if decision.decision not in DECISIONS:
        raise ValueError(f"Unknown ZIP review decision: {decision.decision}")
    if not decision.reason.strip() or not decision.reviewer.strip():
        raise ValueError(f"ZIP review needs a reason and reviewer: {decision.address_id}")
    source = urlparse(decision.source_url)
    if source.scheme not in {"http", "https"} or not source.netloc:
        raise ValueError(f"ZIP review needs a public source URL: {decision.address_id}")
    if decision.decision == "inconclusive":
        if decision.confirmed_zip:
            raise ValueError("Inconclusive ZIP review cannot confirm a ZIP")
        return
    if not decision.confirmed_zip or not re.fullmatch(r"\d{5}", decision.confirmed_zip):
        raise ValueError(f"ZIP review needs a five-digit confirmed ZIP: {decision.address_id}")
    if decision.decision == "input_zip_supported" and decision.confirmed_zip != address.zip.strip():
        raise ValueError("Input-supported decision must confirm the input ZIP")
    if decision.decision == "census_zip_supported" and (
        assessment is None or decision.confirmed_zip not in assessment.matched_zips
    ):
        raise ValueError("Census-supported ZIP needs accepted Census endpoint evidence")
    if decision.decision == "another_zip_supported" and (
        decision.confirmed_zip == address.zip
        or (assessment is not None and decision.confirmed_zip in assessment.matched_zips)
    ):
        raise ValueError("Another-supported ZIP must differ from input and Census ZIPs")
