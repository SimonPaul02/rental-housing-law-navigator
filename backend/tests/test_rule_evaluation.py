"""The decision at the ORM boundary: real Rule and Address rows, no database.

These exercise the path the API takes - `evidence_for` reads the address and
its property facts, `compiled_for` translates the rule, and the evaluator
decides - so a change that works on hand-built evidence but breaks on a real
row shows up here. The vocabulary is the submission's: a pending bill reports
`pending`, not `does_not_apply`.
"""

from __future__ import annotations

import datetime as dt

from app.db.models import Address, AddressJurisdiction, Document, Rule
from app.modules.address_lookup.adapters.property_facts import to_payload
from app.modules.address_lookup.property_facts import PropertyInput, build_property_facts
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult
from app.modules.address_lookup.service import (
    clear_compiled_cache,
    evaluate_rule_for_address,
    evidence_for,
)

AS_OF = dt.date(2026, 10, 1)


def setup_function() -> None:
    # The compiled-rule cache is keyed by rule version, and these tests build
    # many rules with the same id.
    clear_compiled_cache()


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
        coverage_conditions="All residential rental units",
        source_doc_id="DTEST",
        overrides=[],
        conflict_flag=False,
    )
    defaults.update(kw)
    rule = Rule(**defaults)
    source = "\n".join(str(value) for value in (rule.coverage_conditions, rule.exemptions) if value)
    if rule.effective_date:
        source += f"\n\nEffective {rule.effective_date}."
    rule.document = Document(
        doc_id="DTEST",
        jurisdictions=rule.jurisdiction,
        url=rule.source_url,
        body=source,
    )
    return rule


def decide(rule: Rule, address: Address, as_of: dt.date = AS_OF) -> tuple[str, str]:
    outcome = evaluate_rule_for_address(rule, evidence_for(address), as_of)
    return outcome.result, outcome.explanation


# -- status and dates --------------------------------------------------------
def test_a_future_effective_date_reports_not_yet_effective():
    rule = make_rule(status="not_yet_effective", effective_date="2027-07-02")
    result, why = decide(rule, make_address())
    assert result == str(BaseResult.not_yet_effective)
    assert "2027-07-02" in why


def test_the_same_rule_applies_once_its_date_has_passed():
    rule = make_rule(status="not_yet_effective", effective_date="2027-07-02")
    result, _ = decide(rule, make_address(), dt.date(2027, 7, 2))
    assert result == "applies"


def test_an_expired_annual_rate_is_not_displayed_as_current():
    rule = make_rule(
        jurisdiction="Los Angeles, CA",
        level="city",
        category="rent_increase_limits",
        effective_date="2025-07-01",
        key_value="3%",
    )
    rule.document.body = (
        "All residential rental units.\n\n"
        "Annual rent increases for rental units subject to the RSO, effective "
        "July 1, 2025, through June 30, 2026 is 3%."
    )
    outcome = evaluate_rule_for_address(rule, evidence_for(make_address()), AS_OF)
    assert outcome.result == "applies"
    assert outcome.key_value is None
    assert "value for 2026-10-01 is not established" in outcome.explanation


def test_not_yet_effective_without_a_date_is_unknown():
    rule = make_rule(status="not_yet_effective", effective_date=None)
    result, why = decide(rule, make_address())
    assert result == "unknown"
    assert "not January 1" in why


def test_a_month_precision_date_is_unknown_inside_its_month():
    rule = make_rule(status="not_yet_effective", effective_date="2026-10")
    result, why = decide(rule, make_address(), dt.date(2026, 10, 15))
    assert result == "unknown"
    assert "month precision" in why


def test_a_pending_bill_reports_pending_rather_than_not_applying():
    rule = make_rule(status="pending")
    result, why = decide(rule, make_address())
    assert result == str(BaseResult.pending)
    assert "pending" in why.lower()


def test_a_failed_measure_is_reported_nowhere():
    rule = make_rule(status="failed")
    result, why = decide(rule, make_address())
    assert result == str(BaseResult.failed)
    assert "defeated" in why.lower() or "struck" in why.lower()


# -- jurisdiction ------------------------------------------------------------
def test_an_out_of_state_rule_is_marked_out_of_jurisdiction():
    outcome = evaluate_rule_for_address(
        make_rule(jurisdiction="NJ"), evidence_for(make_address()), AS_OF
    )
    assert outcome.result == str(BaseResult.does_not_apply)
    assert outcome.in_jurisdiction is False


def test_a_city_rule_uses_the_legal_city_not_the_postal_city():
    """A Van Nuys mailing address is inside the City of Los Angeles."""
    address = make_address(postal_city="Van Nuys", legal_city="Los Angeles")
    result, why = decide(make_rule(jurisdiction="Los Angeles, CA", level="city"), address)
    assert result == "applies"
    assert "Los Angeles" in why


def test_a_hoboken_rule_does_not_reach_jersey_city():
    address = make_address(postal_city="Jersey City", legal_city="Jersey City", state="NJ")
    outcome = evaluate_rule_for_address(
        make_rule(jurisdiction="Hoboken, NJ", level="city"), evidence_for(address), AS_OF
    )
    assert outcome.in_jurisdiction is False


def test_an_unresolved_city_never_falls_back_to_the_postal_city():
    address = make_address(postal_city="Jersey City", state="NJ")
    address.jurisdiction = None
    outcome = evaluate_rule_for_address(
        make_rule(jurisdiction="Jersey City, NJ", level="city"), evidence_for(address), AS_OF
    )
    assert outcome.result == "unknown"
    assert "legal_city" in outcome.unresolved_fields


# -- facts -------------------------------------------------------------------
def test_a_conflicted_unit_count_cannot_decide_coverage():
    address = make_address(units=15)
    record = build_property_facts(
        [
            PropertyInput(
                address_id=address.address_id, units="15", use_description="Apartment 5 to 14 Units"
            )
        ]
    )[0]
    address.property_facts = to_payload(record)
    outcome = evaluate_rule_for_address(
        make_rule(coverage_conditions="Applies to buildings with 5 or more units."),
        evidence_for(address),
        AS_OF,
    )
    assert outcome.result == "unknown"
    assert "units" in outcome.unresolved_fields


def test_a_missing_unit_count_names_the_field_that_blocked_it():
    address = make_address(postal_city="Berkeley", legal_city="Berkeley", units=None)
    outcome = evaluate_rule_for_address(
        make_rule(
            jurisdiction="Berkeley, CA",
            level="city",
            coverage_conditions="Applies to buildings with 5 or more units.",
        ),
        evidence_for(address),
        AS_OF,
    )
    assert outcome.result == "unknown"
    assert outcome.unresolved_fields == ["units"]


def test_the_san_francisco_cutoff_year_is_unknown():
    address = make_address(postal_city="San Francisco", legal_city="San Francisco", year_built=1979)
    result, why = decide(
        make_rule(
            jurisdiction="San Francisco, CA",
            level="city",
            coverage_conditions="Certificate of occupancy issued on or before 1979-06-13.",
        ),
        address,
    )
    assert result == "unknown"
    assert "certificate" in why.lower()


def test_a_rule_with_no_building_condition_applies_across_its_jurisdiction():
    result, _ = decide(
        make_rule(coverage_conditions="Residential rentals statewide"), make_address()
    )
    assert result == "applies"


def test_a_compound_small_landlord_exemption_stays_pending_without_review():
    rule = make_rule(
        category="security_deposits",
        jurisdiction="NJ",
        exemptions="Owner-occupied premises with 2 or fewer units.",
    )
    address = make_address(postal_city="Newark", legal_city="Newark", state="NJ", units=40)
    result, why = decide(rule, address)
    assert result == "unknown"
    assert "could not be translated" in why


def test_untranslatable_coverage_text_is_unknown_not_applies():
    """The defect the rule adapter exists to remove, at the ORM boundary."""
    rule = make_rule(coverage_conditions="Older buildings within designated districts")
    result, why = decide(rule, make_address())
    assert result == "unknown"
    assert "could not be translated" in why


# -- trace -------------------------------------------------------------------
def test_the_outcome_carries_the_checks_that_produced_it():
    outcome = evaluate_rule_for_address(
        make_rule(coverage_conditions="Applies to buildings with 5 or more units."),
        evidence_for(make_address()),
        AS_OF,
    )
    checks = {c["check"] for c in outcome.checks}
    assert {"geography", "time", "condition"} <= checks
    assert any(c["source_span"] for c in outcome.checks if c["check"] == "condition")
