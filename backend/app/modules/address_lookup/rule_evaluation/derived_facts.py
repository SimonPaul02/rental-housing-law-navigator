"""Facts the parcel record states or implies beyond its numeric columns.

The sample's use description often says more than the unit and year columns:
"Five or more apartments", "APT 7-30 UNITS", "SUBSD HOUSING S- 8", or a New
Jersey building description such as "3S-F-D-6U-NH" (three storeys, frame,
detached, six units). This reads those, and keeps two kinds apart:

  * **stated** - the record says it in so many words (a subsidy code, a unit
    count in the building description). Used like any supplied fact.
  * **presumed** - the record implies it without saying it (an apartment
    building is presumably not a seasonal rental; class 4C means five or more
    units on the parcel). A presumed fact may only *rule out an exemption*;
    it never decides whether a rule covers a building, and every answer that
    leans on one is low confidence and says which.

Pure: evidence in, evidence out.
"""

from __future__ import annotations

import re
from dataclasses import replace

from app.modules.address_lookup.rule_adapter.models import Basis
from app.modules.address_lookup.rule_evaluation.predicates import AddressEvidence, FactValue

PRESUMED = str(Basis.presumed)

_APARTMENT = re.compile(
    r"apart|\bapt\b|-unit-apt|\bflats?\b|5\+\s*units|units?\s+or\s+more|class\s+4c", re.I
)
_COOPERATIVE = re.compile(r"co-?op", re.I)
_NOT_APARTMENTS = re.compile(r"\btic\b|elderly|nursing|dormitor|hotel|motel", re.I)
_SUBSIDISED = re.compile(r"subsd|subsidi[sz]ed|section\s*8|\bs-\s*8\b|affordabl", re.I)

#: Unit floors the description states as a range or a minimum.
_FLOORS = (
    (re.compile(r"\bAPT\s+(\d+)\s*-\s*\d+\s+UNITS\b", re.I), None),  # "APT 7-30 UNITS"
    (re.compile(r"(\d+)\s*\+\s*units\b", re.I), None),  # "(5+ units)"
    (re.compile(r"\b(\d+)\s+units\s+or\s+more\b", re.I), None),  # "15 Units or more"
    (re.compile(r"\bfive\s+or\s+more\s+apartments\b", re.I), 5),
)
#: A New Jersey building description's unit count: "6U" in "3S-F-D-6U-NH",
#: never the "2UG" garage notation of the Newark rows.
_NJ_UNITS = re.compile(r"(?<![A-Z0-9])(\d+)U(?![A-Z])")
_NJ_APARTMENT_CLASS = "4C"


def _fact(value, provenance: str, basis: str | None = None) -> FactValue:
    return FactValue(value, "present", provenance=provenance, basis=basis)


def property_type(description: str, use_code: str, state: str) -> FactValue:
    quoted = f'parcel use description "{description}"'
    if _NOT_APARTMENTS.search(description):
        return FactValue()
    if _COOPERATIVE.search(description):
        return _fact("cooperative", quoted, PRESUMED)
    # Boston's "A/" codes are its apartment rows, as the challenge's own data
    # notes say - "SUBSD HOUSING S- 8" and "LUXURY APARTMENT" alike.
    if (
        _APARTMENT.search(description)
        or (state == "NJ" and use_code.upper() == _NJ_APARTMENT_CLASS)
        or (state == "MA" and use_code.upper().startswith("A/"))
    ):
        return _fact("apartment_building", quoted, PRESUMED)
    return FactValue()


def units_floor(description: str, use_code: str, state: str) -> FactValue:
    """The fewest units the record allows this building to have, if it says."""
    quoted = f'parcel use description "{description}"'
    if state == "NJ":
        stated = [int(n) for n in _NJ_UNITS.findall(description)]
        if stated:
            # Several buildings on one parcel ("2F-4U/2F-2U"): the smallest is
            # the floor for whichever one this is.
            return _fact(min(stated), quoted, PRESUMED)
        if use_code.upper() == _NJ_APARTMENT_CLASS:
            return _fact(5, f"New Jersey property class {use_code} (five or more units)", PRESUMED)
        return FactValue()
    for pattern, fixed in _FLOORS:
        if m := pattern.search(description):
            return _fact(fixed or int(m.group(1)), quoted, PRESUMED)
    return FactValue()


def derive(evidence: AddressEvidence, state: str) -> AddressEvidence:
    """Fill the facts the parcel description states or implies."""
    description = str(evidence.use_description.value or "") if evidence.use_description else ""
    use_code = str(evidence.use_code.value or "") if evidence.use_code.usable else ""
    kind = property_type(description, use_code, state)
    is_building = kind.usable and kind.value in ("apartment_building", "cooperative")
    quoted = f'parcel use description "{description}"'

    if _SUBSIDISED.search(description):
        subsidised = _fact(True, quoted)
    elif is_building:
        subsidised = _fact(False, quoted, PRESUMED)
    else:
        subsidised = FactValue()

    return replace(
        evidence,
        property_type=kind,
        units_floor=units_floor(description, use_code, state),
        building_is_subsidised=subsidised,
        seasonal_rental=_fact(False, quoted, PRESUMED) if is_building else FactValue(),
    )
