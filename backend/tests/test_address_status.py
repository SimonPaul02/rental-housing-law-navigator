"""Address status shown in lists and saved places comes from stored evidence."""

from app.db.models import Address, AddressJurisdiction
from app.modules.address_lookup.router import _address_record
from app.modules.address_lookup.status import jurisdiction_status, zip_discrepancy


def _address(jurisdiction: AddressJurisdiction | None = None) -> Address:
    address = Address(
        address_id="A0001",
        street_address="12 MAIN ST",
        postal_city="Van Nuys",
        state="CA",
    )
    address.jurisdiction = jurisdiction
    return address


def test_not_checked_is_distinct_from_needs_review() -> None:
    assert _address_record(_address()).jurisdiction_status == "not_checked"

    unresolved = AddressJurisdiction(address_id="A0001", method="unresolved")
    record = _address_record(_address(unresolved))
    assert record.jurisdiction_status == "needs_review"
    assert record.legal_city is None


def test_resolved_city_and_zip_flag_are_independent() -> None:
    resolved = AddressJurisdiction(
        address_id="A0001",
        method="geocoder",
        legal_city="Los Angeles",
        resolution_evidence={"zip_assessment": {"status": "mismatch"}},
    )
    record = _address_record(_address(resolved))
    assert record.jurisdiction_status == "resolved"
    assert record.legal_city == "Los Angeles"
    assert record.zip_discrepancy
    assert jurisdiction_status(resolved) == "resolved"
    assert zip_discrepancy(resolved)
