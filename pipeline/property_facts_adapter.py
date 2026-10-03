"""Bridge the existing address resolver's models into the pure property-facts package."""

from __future__ import annotations

from collections.abc import Sequence

from address_resolution.models import AddressInput, ResolvedAddress
from property_facts import PropertyFacts, PropertyInput, build_property_facts


def from_address_inputs(addresses: Sequence[AddressInput]) -> list[PropertyFacts]:
    return build_property_facts([
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
    ])


def from_resolved_addresses(addresses: Sequence[ResolvedAddress]) -> list[PropertyFacts]:
    """Use original source fields even if jurisdiction resolution needs review."""
    for address in addresses:
        if address.address_id != address.input.address_id:
            raise ValueError(f"Resolved/input address_id mismatch: {address.address_id}")
    return from_address_inputs([address.input for address in addresses])
