"""Pure field parsers. No field is inferred from a different semantic kind."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from .models import Fact, PropertyInput, Provenance

_FOUR_DIGIT_YEAR = re.compile(r"[0-9]{4}\Z")
_POSITIVE_INTEGER = re.compile(r"[0-9]+\Z")


def _fact[T](source: PropertyInput, field: str, kind: str, parser: Callable[[str], T]) -> Fact[T]:
    raw = getattr(source, field)
    if not isinstance(raw, str):
        raise TypeError(f"{field} must be a raw string for {source.address_id}")
    value = raw.strip()
    provenance = source.field_provenance.get(
        field, Provenance(source.source_dataset, field, source.retrieved_at)
    )
    if not value:
        return Fact(kind, None, raw, "not_supplied", provenance)
    try:
        parsed = parser(value)
    except ValueError:
        return Fact(kind, None, raw, "invalid", provenance)
    return Fact(kind, parsed, raw, "present", provenance)


def _year(value: str) -> int:
    if not _FOUR_DIGIT_YEAR.fullmatch(value) or int(value) < 1000:
        raise ValueError("year_built must be a four-digit year")
    return int(value)


def _units(value: str) -> int:
    if not _POSITIVE_INTEGER.fullmatch(value) or int(value) == 0:
        raise ValueError("units must be a positive integer")
    return int(value)


def _date(value: str) -> date:
    # fromisoformat accepts compact dates; require the supplied fact to have day precision.
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(value)


def normalize_fields(source: PropertyInput) -> dict[str, Fact]:
    """Parse each supplied field without deriving one kind of fact from another."""
    return {
        "year_built": _fact(source, "year_built", "year_built", _year),
        "units": _fact(source, "units", "unit_count", _units),
        "use_code": _fact(source, "use_code", "source_use_code", str),
        "use_description": _fact(source, "use_description", "source_use_description", str),
        "first_built_date": _fact(source, "first_built_date", "first_built_date", _date),
        "certificate_of_occupancy_date": _fact(
            source, "certificate_of_occupancy_date", "certificate_of_occupancy_date", _date
        ),
    }
