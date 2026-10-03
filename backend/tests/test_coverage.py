"""Coverage evaluator - the edge cases the challenge calls out by name."""

from __future__ import annotations

import pytest

from app.modules.address_lookup.coverage import (
    AddressFacts,
    ExemptionOutcome,
    Outcome,
    evaluate,
    evaluate_exemption,
    jurisdiction_matches,
    parse_conditions,
)


def facts(**kw) -> AddressFacts:
    base = dict(
        address_id="A0001",
        legal_city="Los Angeles",
        legal_state="CA",
        year_built=1960,
        units=32,
    )
    base.update(kw)
    return AddressFacts(**base)


def verdict(conditions: str, **kw) -> tuple[Outcome, list[str]]:
    result = evaluate(parse_conditions(conditions), facts(**kw))
    return result.outcome, result.unresolved


# -- certificate of occupancy -----------------------------------------------
SF = "Covers units with a certificate of occupancy issued on or before 1979-06-13."


def test_co_cutoff_year_itself_is_unknown():
    """'A building in the cutoff year should be unknown' - year built is not
    the certificate date, so 1979 cannot be resolved either way."""
    outcome, unresolved = verdict(SF, year_built=1979)
    assert outcome is Outcome.unknown
    assert "certificate_of_occupancy_date" in unresolved


def test_co_clearly_before_applies():
    assert verdict(SF, year_built=1925)[0] is Outcome.applies


def test_co_clearly_after_fails():
    assert verdict(SF, year_built=2015)[0] is Outcome.fails


def test_co_without_year_built_is_unknown():
    assert verdict(SF, year_built=None)[0] is Outcome.unknown


def test_la_cutoff_year_is_unknown():
    la = "Applies where the certificate of occupancy was issued on or before 1978-10-01."
    assert verdict(la, year_built=1978)[0] is Outcome.unknown
    assert verdict(la, year_built=1950)[0] is Outcome.applies


# -- missing data in the sample ---------------------------------------------
def test_missing_units_is_unknown_not_a_guess():
    """Berkeley, Jersey City, Newark and most Hoboken rows have no unit count."""
    outcome, unresolved = verdict("Applies to buildings with 5 or more units.", units=None)
    assert outcome is Outcome.unknown
    assert unresolved == ["units"]


def test_units_threshold_evaluates_when_present():
    assert verdict("Applies to buildings with 5 or more units.", units=32)[0] is Outcome.applies
    assert verdict("Applies to buildings with 5 or more units.", units=3)[0] is Outcome.fails


def test_owner_type_alone_is_unresolvable():
    """Owner names are deliberately excluded, so an owner-only condition
    can never be settled from this data."""
    outcome, unresolved = verdict("Applies only where the owner is a natural person.")
    assert outcome is Outcome.unknown
    assert "owner_identity" in unresolved


def test_no_parsable_condition_yields_no_predicates():
    assert parse_conditions("Residential rentals statewide") == []
    assert parse_conditions(None) == []


def test_definite_failure_beats_unknown():
    """If the building plainly misses a threshold, a missing owner name is
    irrelevant - we should say does_not_apply, not unknown."""
    outcome, _ = verdict(
        "Applies to buildings with 50 or more units; exempts owner-occupied premises.",
        units=4,
    )
    assert outcome is Outcome.fails


# -- jurisdiction ------------------------------------------------------------
def test_state_rule_matches_on_state():
    ok, _ = jurisdiction_matches(rule_jurisdiction="CA", rule_level="state", facts=facts())
    assert ok


def test_state_rule_rejects_other_state():
    ok, _ = jurisdiction_matches(rule_jurisdiction="NJ", rule_level="state", facts=facts())
    assert not ok


def test_city_rule_matches_legal_city_not_postal_city():
    """Van Nuys mail is City of Los Angeles law."""
    ok, _ = jurisdiction_matches(
        rule_jurisdiction="Los Angeles, CA",
        rule_level="city",
        facts=facts(legal_city="Los Angeles"),
    )
    assert ok


def test_city_rule_rejects_neighbouring_city():
    ok, _ = jurisdiction_matches(
        rule_jurisdiction="Hoboken, NJ",
        rule_level="city",
        facts=facts(legal_city="Jersey City", legal_state="NJ"),
    )
    assert not ok


@pytest.mark.parametrize("city", ["los angeles", "Los Angeles", "LOS ANGELES"])
def test_city_match_is_case_insensitive(city):
    ok, _ = jurisdiction_matches(
        rule_jurisdiction="Los Angeles, CA", rule_level="city", facts=facts(legal_city=city)
    )
    assert ok


# -- exemptions carry the opposite polarity to coverage conditions ----------
def test_small_landlord_exemption_cannot_apply_to_a_large_building():
    """The challenge asks us to explain why an exception can't apply rather
    than giving up: a 32-unit building defeats a '2 or fewer units' exemption
    no matter who owns it."""
    e = evaluate_exemption(
        parse_conditions("Owner-occupied premises with 2 or fewer units."),
        facts(units=32),
    )
    assert e.outcome is ExemptionOutcome.not_exempt


def test_exemption_unknown_when_the_building_might_qualify():
    e = evaluate_exemption(
        parse_conditions("Owner-occupied premises with 2 or fewer units."),
        facts(units=2),
    )
    assert e.outcome is ExemptionOutcome.unknown
    assert "owner_identity" in e.unresolved


def test_no_exemption_text_means_not_exempt():
    assert evaluate_exemption(parse_conditions(None), facts()).outcome is (
        ExemptionOutcome.not_exempt
    )


def test_exemption_that_plainly_matches_is_exempt():
    e = evaluate_exemption(
        parse_conditions("Exempts buildings built after 2020."), facts(year_built=2023)
    )
    assert e.outcome is ExemptionOutcome.exempt
