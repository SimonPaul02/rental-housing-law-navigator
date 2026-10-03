"""The full (rule, address, date) decision, including status and date gating.

These use plain ORM instances - no database needed, because
evaluate_rule_for_address is pure.
"""

from __future__ import annotations

import datetime as dt

from app.db.models import Address, AddressJurisdiction, Rule
from app.modules.address_lookup.adapters.property_facts import to_payload
from app.modules.address_lookup.property_facts import PropertyInput, build_property_facts
from app.modules.address_lookup.service import (
    evaluate_rule_for_address,
    facts_for,
    parse_effective_date,
)

AS_OF = dt.date(2026, 10, 1)


def make_address(**kw) -> Address:
    defaults = dict(
        address_id="A0001",
        street_address="6238 DE LONGPRE AVE",
        postal_city="Los Angeles",
        state="CA",
        zip="90028",
        year_built=1927,
        units=32,
    )
    defaults.update(kw)
    legal_city = defaults.pop("legal_city", defaults["postal_city"])
    address = Address(**defaults)
    address.jurisdiction = AddressJurisdiction(
        address_id=address.address_id,
        legal_city=legal_city,
        legal_state=address.state,
        method="census",
        confidence=0.95,
    )
    return address


def make_rule(**kw) -> Rule:
    defaults = dict(
        team_rule_id="r-0001",
        jurisdiction="CA",
        level="state",
        category="algorithmic_rent_setting",
        status="in_force",
        title="Test rule",
        requirement="Does a thing.",
        citation="Cal. Civ. Code § 1",
        source_url="https://example.gov/1",
        quoted_span="x" * 25,
        overrides=[],
        conflict_flag=False,
    )
    defaults.update(kw)
    return Rule(**defaults)


def decide(rule: Rule, address: Address, as_of: dt.date = AS_OF) -> tuple[str, str]:
    outcome = evaluate_rule_for_address(rule, facts_for(address), as_of)
    return outcome.result, outcome.explanation


# -- effective dates ---------------------------------------------------------
def test_parse_partial_effective_dates():
    assert parse_effective_date("2027") == dt.date(2027, 1, 1)
    assert parse_effective_date("2027-07") == dt.date(2027, 7, 1)
    assert parse_effective_date("2027-07-02") == dt.date(2027, 7, 2)
    assert parse_effective_date(None) is None
    assert parse_effective_date("not a date") is None


def test_future_effective_date_does_not_apply():
    rule = make_rule(status="not_yet_effective", effective_date="2027-07-02")
    result, why = decide(rule, make_address())
    assert result == "does_not_apply"
    assert "not yet effective" in why.lower()


def test_same_rule_applies_after_its_effective_date():
    rule = make_rule(status="not_yet_effective", effective_date="2027-07-02")
    result, _ = decide(rule, make_address(), dt.date(2027, 7, 2))
    assert result == "applies"


def test_not_yet_effective_without_a_date_is_unknown():
    rule = make_rule(status="not_yet_effective", effective_date=None)
    result, _ = decide(rule, make_address())
    assert result == "unknown"


# -- status ------------------------------------------------------------------
def test_pending_bill_does_not_apply_but_says_so():
    rule = make_rule(status="pending")
    result, why = decide(rule, make_address())
    assert result == "does_not_apply"
    assert "pending" in why.lower()


def test_failed_measure_does_not_apply():
    rule = make_rule(status="failed")
    result, why = decide(rule, make_address())
    assert result == "does_not_apply"
    assert "failed" in why.lower() or "defeated" in why.lower()


# -- jurisdiction ------------------------------------------------------------
def test_out_of_state_rule_is_marked_out_of_jurisdiction():
    rule = make_rule(jurisdiction="NJ")
    outcome = evaluate_rule_for_address(rule, facts_for(make_address()), AS_OF)
    assert outcome.result == "does_not_apply"
    assert outcome.in_jurisdiction is False


def test_city_rule_uses_legal_city_not_postal_city():
    """A Van Nuys mailing address is inside the City of Los Angeles."""
    address = make_address(postal_city="Van Nuys", legal_city="Los Angeles")
    rule = make_rule(jurisdiction="Los Angeles, CA", level="city")
    result, _ = decide(rule, address)
    assert result == "applies"


def test_hoboken_rule_does_not_reach_jersey_city():
    address = make_address(postal_city="Jersey City", legal_city="Jersey City", state="NJ")
    rule = make_rule(jurisdiction="Hoboken, NJ", level="city")
    outcome = evaluate_rule_for_address(rule, facts_for(address), AS_OF)
    assert outcome.in_jurisdiction is False


def test_unresolved_city_does_not_assume_postal_city():
    address = make_address(postal_city="Jersey City", state="NJ")
    address.jurisdiction = None
    rule = make_rule(jurisdiction="Jersey City, NJ", level="city")
    outcome = evaluate_rule_for_address(rule, facts_for(address), AS_OF)
    assert outcome.result == "unknown"
    assert outcome.unresolved_fields == ["legal_city"]


def test_conflicted_unit_count_cannot_decide_coverage():
    address = make_address(units=15)
    record = build_property_facts(
        [
            PropertyInput(
                address_id=address.address_id,
                units="15",
                use_description="Apartment 5 to 14 Units",
            )
        ]
    )[0]
    address.property_facts = to_payload(record)
    rule = make_rule(coverage_conditions="Applies to buildings with 5 or more units.")
    outcome = evaluate_rule_for_address(rule, facts_for(address), AS_OF)
    assert outcome.result == "unknown"
    assert "units" in outcome.unresolved_fields


# -- coverage interaction ----------------------------------------------------
def test_sf_cutoff_year_building_is_unknown():
    address = make_address(postal_city="San Francisco", legal_city="San Francisco", year_built=1979)
    rule = make_rule(
        jurisdiction="San Francisco, CA",
        level="city",
        coverage_conditions="Certificate of occupancy issued on or before 1979-06-13.",
    )
    result, why = decide(rule, address)
    assert result == "unknown"
    assert "certificate" in why.lower()


def test_missing_units_yields_unknown_with_the_field_named():
    address = make_address(postal_city="Berkeley", legal_city="Berkeley", units=None)
    rule = make_rule(
        jurisdiction="Berkeley, CA",
        level="city",
        coverage_conditions="Applies to buildings with 5 or more units.",
    )
    outcome = evaluate_rule_for_address(rule, facts_for(address), AS_OF)
    assert outcome.result == "unknown"
    assert outcome.unresolved_fields == ["units"]


def test_rule_with_no_conditions_applies_statewide():
    rule = make_rule(coverage_conditions="Residential rentals statewide")
    result, _ = decide(rule, make_address())
    assert result == "applies"


def test_large_building_defeats_small_landlord_exemption():
    rule = make_rule(
        category="security_deposits",
        jurisdiction="NJ",
        exemptions="Owner-occupied premises with 2 or fewer units.",
    )
    address = make_address(postal_city="Newark", legal_city="Newark", state="NJ", units=40)
    result, why = decide(rule, address)
    assert result == "applies"
    assert "exemption cannot apply" in why
