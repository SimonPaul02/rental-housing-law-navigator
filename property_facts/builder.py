"""Public, pure transformation from property inputs to typed fact records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .models import PropertyFacts, PropertyInput
from .normalize import normalize_fields
from .validate import validate_fields


def build_property_facts(inputs: Sequence[PropertyInput]) -> list[PropertyFacts]:
    """Return one record per input in order, without IO or jurisdiction assumptions."""
    seen_ids: set[str] = set()
    records: list[PropertyFacts] = []
    for source in inputs:
        if not source.address_id or not source.address_id.strip():
            raise ValueError("Missing address_id")
        if source.address_id in seen_ids:
            raise ValueError(f"Duplicate address_id: {source.address_id}")
        seen_ids.add(source.address_id)
        fields = normalize_fields(source)
        warnings = validate_fields(source, fields)
        if "units_use_description_conflict" in warnings:
            fields["units"] = replace(fields["units"], status="conflicted")
            fields["use_description"] = replace(fields["use_description"], status="conflicted")
        for field in ("year_built", "first_built_date", "certificate_of_occupancy_date"):
            if f"{field}_after_retrieval" in warnings:
                fields[field] = replace(fields[field], status="conflicted")
        records.append(PropertyFacts(
            address_id=source.address_id,
            record_scope=source.record_scope,
            year_built=fields["year_built"],
            units=fields["units"],
            use_code=fields["use_code"],
            use_description=fields["use_description"],
            first_built_date=fields["first_built_date"],
            certificate_of_occupancy_date=fields["certificate_of_occupancy_date"],
            warnings=warnings,
        ))
    return records
