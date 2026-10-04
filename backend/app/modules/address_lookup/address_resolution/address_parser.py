"""Parse a source address without treating a partial match as a whole property."""

from __future__ import annotations

import re

from .models import AddressShape

_NUMBER = r"\d+(?:\.\d+)?[A-Za-z]?"
_RANGE = re.compile(rf"^\s*({_NUMBER})\s*-\s*({_NUMBER})-?\s+(.+?)\s*$")
_COMPOUND_SAME_STREET = re.compile(rf"^\s*({_NUMBER})\s*&\s*({_NUMBER})\s+(.+?)\s*$")
_COMPOUND_CROSS_STREET = re.compile(rf"^\s*({_NUMBER})\s+([^/]+?)\s*/\s*({_NUMBER})\s+(.+?)\s*$")
_SINGLE = re.compile(rf"^\s*({_NUMBER})\s+(.+?)\s*$")


def parse_address(street_address: str) -> AddressShape:
    """Return complete query components and the policy-relevant input shape."""
    raw = street_address.strip()
    if not raw:
        return AddressShape(street_address, "unsupported", (), "Blank street address")

    cross = _COMPOUND_CROSS_STREET.fullmatch(raw)
    if cross:
        first, first_street, second, second_street = cross.groups()
        return AddressShape(
            street_address,
            "compound_cross_street",
            (f"{first} {first_street.strip()}", f"{second} {second_street.strip()}"),
        )

    same = _COMPOUND_SAME_STREET.fullmatch(raw)
    if same:
        first, second, street = same.groups()
        return AddressShape(
            street_address,
            "compound_same_street",
            (f"{first} {street}", f"{second} {street}"),
        )

    house_range = _RANGE.fullmatch(raw)
    if house_range:
        first, second, street = house_range.groups()
        if "/" in street or "&" in street:
            return AddressShape(street_address, "unsupported", (), "Nested address separators")
        components = tuple(dict.fromkeys((f"{first} {street}", f"{second} {street}")))
        kind = "fractional_range" if "." in first or "." in second else "same_street_range"
        note = "Duplicate range endpoints" if len(components) == 1 else ""
        return AddressShape(street_address, kind, components, note)

    if "/" in raw or "&" in raw:
        return AddressShape(street_address, "unsupported", (), "Unparsed address separator")
    single = _SINGLE.fullmatch(raw)
    if single:
        return AddressShape(street_address, "single", (raw,))
    if re.match(r"^\s*\d", raw):
        return AddressShape(street_address, "unsupported", (), "Unsupported house-number syntax")
    return AddressShape(street_address, "no_house_number", (raw,))
