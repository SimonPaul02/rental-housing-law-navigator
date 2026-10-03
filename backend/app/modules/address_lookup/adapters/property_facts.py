"""Bridge the existing address resolver's models into the pure property-facts package."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING

from app.modules.address_lookup.address_resolution.models import AddressInput, ResolvedAddress
from app.modules.address_lookup.property_facts import (
    Fact,
    PropertyFacts,
    PropertyInput,
    Provenance,
    build_property_facts,
)

if TYPE_CHECKING:
    from app.db.models import Address


def from_address_inputs(addresses: Sequence[AddressInput]) -> list[PropertyFacts]:
    return build_property_facts(
        [
            PropertyInput(
                address_id=address.address_id,
                year_built=address.year_built,
                units=address.units,
                use_code=address.use_code,
                use_description=address.use_description,
                source_dataset=address.source_dataset,
                retrieved_at=address.retrieved_at,
            )
            for address in addresses
        ]
    )


def from_resolved_addresses(addresses: Sequence[ResolvedAddress]) -> list[PropertyFacts]:
    """Use original source fields even if jurisdiction resolution needs review."""
    for address in addresses:
        if address.address_id != address.input.address_id:
            raise ValueError(f"Resolved/input address_id mismatch: {address.address_id}")
    return from_address_inputs([address.input for address in addresses])


def to_payload(record: PropertyFacts) -> dict:
    """JSON-safe snapshot for the database and API audit view."""
    payload = asdict(record)
    for field in ("first_built_date", "certificate_of_occupancy_date"):
        value = payload[field]["value"]
        if isinstance(value, date):
            payload[field]["value"] = value.isoformat()
    return payload


def from_payload(payload: dict) -> PropertyFacts:
    def fact(field: str) -> Fact:
        data = payload[field]
        value = data["value"]
        if field in {"first_built_date", "certificate_of_occupancy_date"} and value is not None:
            value = date.fromisoformat(value)
        return Fact(
            kind=data["kind"],
            value=value,
            raw_value=data["raw_value"],
            status=data["status"],
            provenance=Provenance(**data["provenance"]),
        )

    return PropertyFacts(
        address_id=payload["address_id"],
        record_scope=payload["record_scope"],
        year_built=fact("year_built"),
        units=fact("units"),
        use_code=fact("use_code"),
        use_description=fact("use_description"),
        first_built_date=fact("first_built_date"),
        certificate_of_occupancy_date=fact("certificate_of_occupancy_date"),
        warnings=tuple(payload.get("warnings", ())),
    )


def from_db_address(address: Address) -> PropertyFacts:
    """Read persisted evidence; support rows seeded before this migration."""
    if address.property_facts:
        record = from_payload(address.property_facts)
        if record.address_id != address.address_id:
            raise ValueError(f"Property fact/address mismatch: {address.address_id}")
        return record
    return build_property_facts(
        [
            PropertyInput(
                address_id=address.address_id,
                year_built=str(address.year_built) if address.year_built is not None else "",
                units=str(address.units) if address.units is not None else "",
                use_code=address.use_code or "",
                use_description=address.use_description or "",
                source_dataset=address.source_dataset or "",
                retrieved_at=address.retrieved_at or "",
            )
        ]
    )[0]
