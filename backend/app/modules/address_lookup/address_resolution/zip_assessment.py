"""Assess postal ZIP evidence independently of the legal-city decision."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import AcceptedEndpoint, AddressInput, ZipAssessment

_ZIP_PREFIXES = {"CA": ("9",), "NJ": ("07", "08"), "MA": ("01", "02", "055")}


def zip_is_plausible(zip_code: str, state: str) -> bool:
    """A coarse state check, not proof that a ZIP belongs to a municipality."""
    return bool(re.fullmatch(r"\d{5}", zip_code)) and zip_code.startswith(
        _ZIP_PREFIXES.get(state.upper(), tuple(str(i) for i in range(10)))
    )


def assess_zip(
    address: AddressInput,
    accepted_endpoints: Sequence[AcceptedEndpoint],
    expected_endpoint_count: int,
) -> ZipAssessment:
    """Compare raw ZIP with accepted geocoder evidence; never change jurisdiction."""
    input_zip = address.zip.strip()
    matched_zips = tuple(
        dict.fromkeys(
            endpoint.candidate.zip for endpoint in accepted_endpoints if endpoint.candidate.zip
        )
    )
    evidence_complete = (
        expected_endpoint_count > 0
        and len(accepted_endpoints) == expected_endpoint_count
        and all(endpoint.candidate.zip for endpoint in accepted_endpoints)
    )

    if not input_zip:
        status, reason = "missing_input", "The source record has no ZIP."
    elif not zip_is_plausible(input_zip, address.state):
        status, reason = (
            "invalid_for_state",
            "The input ZIP fails the five-digit or state-prefix check.",
        )
    elif not evidence_complete:
        status, reason = (
            "insufficient_evidence",
            "Not every address endpoint has an accepted match with a ZIP.",
        )
    elif all(endpoint.candidate.zip == input_zip for endpoint in accepted_endpoints):
        status, reason = "matches_all", "The input ZIP agrees with every accepted endpoint."
    elif input_zip in matched_zips:
        status, reason = (
            "matches_some_endpoints",
            "The input ZIP agrees with some, but not all, accepted endpoints.",
        )
    else:
        status, reason = "mismatch", "The input ZIP differs from every accepted endpoint."

    return ZipAssessment(
        status=status,
        input_zip=input_zip,
        matched_zips=matched_zips,
        expected_endpoints=expected_endpoint_count,
        accepted_endpoints=len(accepted_endpoints),
        reason=reason,
        source_dataset=address.source_dataset,
        source_retrieved_at=address.retrieved_at,
    )
