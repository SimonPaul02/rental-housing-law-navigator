"""Conservative cross-field checks, separate from normalization."""

from __future__ import annotations

import re
from datetime import datetime

from .models import Fact, PropertyInput

_RANGE = re.compile(r"\b(\d+)\s*(?:to|-)\s*(\d+)\s*[- ]?\s*units?\b", re.I)
_AT_MOST = re.compile(r"\b(\d+)\s*units?\s+or\s+less\b", re.I)
_GREATER_THAN = re.compile(r">\s*(\d+)\s*[- ]?\s*units?\b", re.I)


def _description_disagrees(units: int, description: str) -> bool:
    """Only read explicit numeric bounds; never derive an exact count or decode a use code."""
    for match in _RANGE.finditer(description):
        low, high = map(int, match.groups())
        if low > high or not low <= units <= high:
            return True
    for match in _AT_MOST.finditer(description):
        if units > int(match.group(1)):
            return True
    for match in _GREATER_THAN.finditer(description):
        if units <= int(match.group(1)):
            return True
    return False


def validate_fields(source: PropertyInput, facts: dict[str, Fact]) -> tuple[str, ...]:
    warnings = [f"invalid_{field}" for field, fact in facts.items() if fact.status == "invalid"]
    if not source.source_dataset.strip():
        warnings.append("missing_source_dataset")
    if not source.retrieved_at.strip():
        warnings.append("missing_retrieved_at")
    else:
        try:
            datetime.fromisoformat(source.retrieved_at.strip().replace("Z", "+00:00"))
        except ValueError:
            warnings.append("invalid_retrieved_at")

    for field in ("year_built", "first_built_date", "certificate_of_occupancy_date"):
        fact = facts[field]
        observed_raw = fact.provenance.retrieved_at.strip()
        if not observed_raw:
            continue
        try:
            observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
        except ValueError:
            if field in source.field_provenance:
                warnings.append(f"invalid_{field}_retrieved_at")
            continue
        if fact.value is not None:
            after_retrieval = (
                fact.value > observed.year
                if field == "year_built"
                else fact.value > observed.date()
            )
            if after_retrieval:
                warnings.append(f"{field}_after_retrieval")

    units = facts["units"].value
    description = facts["use_description"].value
    if units is not None and description is not None and _description_disagrees(units, description):
        warnings.append("units_use_description_conflict")
    return tuple(warnings)
