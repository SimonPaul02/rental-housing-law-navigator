"""The rule adapter: what it translates, and what it refuses to translate.

The case that matters most is the last one in the first group: unrecognised
legal text must not become unconditional coverage. That was the live defect -
the old runtime parser returned no predicates for prose it did not match, and
no predicates read as nothing to test, which read as `applies`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from app.modules.address_lookup.rule_adapter import compiler
from app.modules.address_lookup.rule_adapter.models import (
    Atom,
    Basis,
    CoverageBasis,
    Field,
    InvalidCompilation,
    Op,
    Origin,
    ReviewState,
    SourceAnchor,
    rule_version_hash,
    validate_atom,
)
from app.modules.address_lookup.rule_adapter.review import (
    ReviewStore,
    compiled_from_json,
)


@dataclass
class FakeRule:
    team_rule_id: str = "r-test"
    jurisdiction: str = "San Francisco, CA"
    level: str = "city"
    category: str = "rent_increase_limits"
    status: str = "in_force"
    title: str = "Rent cap"
    requirement: str = "Rent may not rise more than the allowable increase."
    key_value: str | None = "5%"
    coverage_conditions: str | None = None
    exemptions: str | None = None
    interaction: str | None = None
    overrides: list[str] | None = None
    effective_date: str | None = None
    citation: str = "SF Admin. Code § 37.3"
    quoted_span: str = "A landlord may not increase rent more than the allowable amount."
    source_doc_id: str | None = "D078"
    source_url: str = "https://example.gov/d078"


def compile_one(**kwargs):
    record = FakeRule(**kwargs)
    source = "\n".join(str(x) for x in (record.coverage_conditions, record.exemptions) if x)
    return compiler.compile_rule(record, source_text=source)


# -- what gets translated ---------------------------------------------------
def test_certificate_cutoff_becomes_a_dated_condition():
    rule, _ = compile_one(
        coverage_conditions=(
            "Buildings with a certificate of occupancy issued on or before 1979-06-13"
        )
    )
    atoms = list(rule.coverage.atoms())
    assert len(atoms) == 1
    assert atoms[0].field is Field.certificate_of_occupancy_date
    assert atoms[0].op is Op.lte
    assert atoms[0].value == dt.date(1979, 6, 13)
    assert "1979-06-13" in atoms[0].anchor.source_span


def test_unit_threshold_keeps_its_boundary():
    at_least, _ = compile_one(coverage_conditions="Buildings with 5 or more dwelling units")
    assert [(a.op, a.value) for a in at_least.coverage.atoms()] == [(Op.gte, 5)]
    fewer, _ = compile_one(exemptions="Buildings with 2 or fewer units")
    assert [(a.op, a.value) for a in fewer.exemptions.atoms()] == [(Op.lte, 2)]


def test_an_exemption_list_stays_a_disjunction():
    """The flattening bug this guards against would let a missing owner name
    defeat the seasonal branch too."""
    rule, _ = compile_one(
        exemptions="Owner-occupied premises with 2 or fewer units; seasonal rentals"
    )
    assert rule.exemptions.kind == "any"
    owner, seasonal = rule.exemptions.children
    assert owner.kind == "all"
    assert [(a.field, a.op, a.value) for a in owner.children] == [
        (Field.owner_occupied, Op.is_true, None),
        (Field.units, Op.lte, 2),
    ]
    assert (seasonal.field, seasonal.op) == (Field.seasonal_rental, Op.is_true)
    # Read by the machine, not verified against the source: low confidence.
    assert all(a.basis is Basis.machine_read for a in rule.exemptions.atoms())


def test_unconditional_prose_is_not_an_unmapped_clause():
    rule, _ = compile_one(coverage_conditions="Residential rentals statewide")
    assert rule.coverage.is_empty
    assert not rule.has_unmapped
    assert rule.review_state is ReviewState.machine_verified


# -- what gets refused ------------------------------------------------------
def test_unrecognised_coverage_text_is_recorded_not_dropped():
    """The defect this replaced: no predicate meant nothing to test, which
    meant `applies`."""
    rule, _ = compile_one(coverage_conditions="Older buildings in designated districts")
    assert rule.has_unmapped
    assert rule.review_state is ReviewState.needs_review
    assert "Older buildings" in rule.unmapped_text[0].text


def test_unrecognised_exemption_text_is_recorded():
    rule, _ = compile_one(exemptions="Premises subject to a regulatory agreement of any kind")
    assert rule.has_unmapped
    assert rule.unmapped_text[0].origin is Origin.exemption


def test_a_certificate_cutoff_without_a_day_is_unknown_not_ignored():
    rule, _ = compile_one(
        coverage_conditions="Buildings whose certificate of occupancy predates the cutoff"
    )
    assert rule.coverage.is_empty
    assert rule.has_unmapped


@pytest.mark.parametrize(
    "atom,problem",
    [
        (Atom("a", Field.units, Op.lte, "two", SourceAnchor("2 units")), "integer"),
        (Atom("a", Field.units, Op.is_true, None, SourceAnchor("units")), "boolean"),
        (Atom("a", Field.owner_occupied, Op.lte, 2, SourceAnchor("owner")), "is_true/is_false"),
        (Atom("a", Field.legal_city, Op.lt, "San Jose", SourceAnchor("city")), "ordered"),
        (Atom("a", Field.units, Op.lte, 2, None), "source span"),
    ],
)
def test_invalid_atoms_are_rejected(atom, problem):
    with pytest.raises(InvalidCompilation) as caught:
        validate_atom(atom)
    assert problem in str(caught.value)


# -- effective dates --------------------------------------------------------
def test_a_day_is_a_point_and_a_month_is_an_interval():
    day = compiler.parse_effective_date("2026-07-20")
    assert (day.earliest, day.latest, day.is_exact) == (
        dt.date(2026, 7, 20),
        dt.date(2026, 7, 20),
        True,
    )
    month = compiler.parse_effective_date("2026-07")
    assert (month.earliest, month.latest) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert not month.is_exact
    year = compiler.parse_effective_date("2026")
    assert (year.earliest, year.latest) == (dt.date(2026, 1, 1), dt.date(2026, 12, 31))


def test_a_second_date_in_prose_becomes_a_second_candidate():
    record = FakeRule(
        effective_date="2027-07-01", interaction="Operative from 2027-09-01 in some counties."
    )
    rule, _ = compiler.compile_rule(
        record,
        source_text="The law is effective 2027-07-01. Operative from 2027-09-01 in some counties.",
    )
    assert len(rule.effective_dates) == 2
    assert any("more than one candidate" in n for n in rule.notes)


def test_annual_rate_period_is_not_a_rule_effective_date():
    record = FakeRule(
        effective_date="2025-07-01",
        key_value="3%",
        interaction="Annual rate from 2025-07-01 through 2026-06-30.",
    )
    source = (
        "Annual rent increases for rental units subject to the RSO, effective "
        "July 1, 2025, through June 30, 2026 is 3%."
    )
    compiled, _ = compiler.compile_rule(record, source_text=source)
    assert compiled.effective_dates == ()
    assert not compiled.effective_date_unresolved
    assert compiled.key_value_period.start == dt.date(2025, 7, 1)
    assert compiled.key_value_period.end == dt.date(2026, 6, 30)


def test_rate_value_before_period_is_separate_from_law_effective_date():
    record = FakeRule(effective_date="2026-03-01", key_value="1.6%")
    source = (
        "For March 1, 2026 - February 28, 2027\n"
        "Allowable Rent Increase:\n1.6% for March 1, 2026 – February 28, 2027\n"
        "Security Deposit Interest:\n4.2% for March 1, 2026 – February 28, 2027"
    )
    compiled, _ = compiler.compile_rule(record, source_text=source)
    assert compiled.effective_dates == ()
    assert not compiled.effective_date_unresolved
    assert compiled.key_value_period.start == dt.date(2026, 3, 1)
    assert compiled.key_value_period.end == dt.date(2027, 2, 28)


def test_unanchored_record_date_needs_review():
    record = FakeRule(effective_date="2027-07-01")
    compiled, _ = compiler.compile_rule(record, source_text="The rule covers rental units.")
    assert compiled.effective_date_unresolved
    assert not compiled.effective_dates


# -- versioning -------------------------------------------------------------
def test_the_hash_changes_when_the_text_does_but_not_when_the_id_repeats():
    first = rule_version_hash(FakeRule(coverage_conditions="2 or fewer units"))
    same = rule_version_hash(FakeRule(coverage_conditions="2 or fewer units"))
    corrected = rule_version_hash(FakeRule(coverage_conditions="3 or fewer units"))
    assert first == same
    assert first != corrected


def test_a_stored_revision_is_ignored_once_the_rule_text_changes(tmp_path):
    """Approved interpretation must not carry over to text nobody reviewed."""
    store = ReviewStore(path=tmp_path / "compiled.json")
    rule, _ = compile_one(coverage_conditions="2 or fewer units")
    store.put(rule)
    assert store.get(rule.team_rule_id, rule.rule_version_hash, rule.source_hash) is not None

    corrected, _ = compile_one(coverage_conditions="3 or fewer units")
    assert (
        store.get(corrected.team_rule_id, corrected.rule_version_hash, corrected.source_hash)
        is None
    )


def test_a_compiled_rule_round_trips_through_json():
    rule, _ = compile_one(
        coverage_conditions="Certificate of occupancy issued on or before 1979-06-13",
        exemptions="Owner-occupied premises with 2 or fewer units; seasonal rentals",
        effective_date="2026-07",
    )
    again = compiled_from_json(rule.to_json())
    assert again.to_json() == rule.to_json()


# -- proposals --------------------------------------------------------------
def test_a_proposal_must_quote_text_that_exists():
    rule, _ = compile_one(coverage_conditions="Older buildings in designated districts")
    invented = {
        "clauses": [
            {
                "text": rule.unmapped_text[0].text,
                "atoms": [
                    {
                        "id": "p1",
                        "field": "year_built",
                        "op": "lte",
                        "value": 1979,
                        "source_span": "built on or before 1979",
                    },
                ],
            }
        ]
    }
    coverage, _, unmapped, _scope = compiler.apply_proposal(
        FakeRule(coverage_conditions="Older buildings in designated districts"),
        invented,
        rule.coverage,
        rule.exemptions,
        rule.unmapped_text,
    )
    assert list(coverage.atoms()) == [], "a span that is not in the source is dropped"
    assert len(unmapped) == 1, "and the clause stays unmapped"


def test_a_proposal_that_quotes_real_text_is_accepted():
    record = FakeRule(coverage_conditions="Buildings of the older vintage in the city")
    rule, _ = compiler.compile_rule(record)
    good = {
        "clauses": [
            {
                "text": rule.unmapped_text[0].text,
                "atoms": [
                    {
                        "id": "p1",
                        "field": "units",
                        "op": "gte",
                        "value": 2,
                        "source_span": "older vintage",
                    },
                ],
            }
        ]
    }
    coverage, _, unmapped, _scope = compiler.apply_proposal(
        record, good, rule.coverage, rule.exemptions, rule.unmapped_text
    )
    assert list(coverage.atoms()) == []
    assert len(unmapped) == 1, "older vintage cannot support an invented threshold"


# -- issue keys -------------------------------------------------------------
def test_issue_key_is_narrower_than_category():
    """Two rent rules can govern different obligations, so only one of them can
    supersede another on a cap."""
    cap = compiler.issue_key_for(FakeRule(title="Annual allowable increase", key_value="5%"))
    freq = compiler.issue_key_for(
        FakeRule(
            title="Number of increases", requirement="No more than twice in any 12-month period"
        )
    )
    assert cap == "rent_increase_cap"
    assert freq == "rent_increase_frequency"
    assert cap != freq


def test_a_scope_only_clause_can_be_cleared_but_only_with_a_real_span():
    """The one verdict that can turn an unknown into an applies, so it is the
    one that most needs its span checked."""
    # A clause the deterministic reader knows is set aside on its own reading.
    known = FakeRule(coverage_conditions="Rental dwelling units offered by a housing provider")
    rule, _ = compiler.compile_rule(known)
    assert not rule.has_unmapped and rule.review_state is ReviewState.machine_classified
    assert rule.classified[0].kind == "scope"

    # One it does not know stays unmapped, and only a real span earns an AI note.
    record = FakeRule(coverage_conditions="Premises in designated districts")
    rule, _ = compiler.compile_rule(record)
    assert rule.has_unmapped

    honest = {
        "clauses": [
            {
                "text": rule.unmapped_text[0].text,
                "verdict": "no_building_condition",
                "source_span": "Premises in designated districts",
                "atoms": [],
            }
        ]
    }
    coverage, _, unmapped, scope = compiler.apply_proposal(
        record,
        honest,
        rule.coverage,
        rule.exemptions,
        rule.unmapped_text,
        source_text=record.coverage_conditions,
    )
    assert len(unmapped) == 1
    assert coverage.is_empty
    assert scope and "designated districts" in scope[0]

    invented = {
        "clauses": [
            {
                "text": rule.unmapped_text[0].text,
                "verdict": "no_building_condition",
                "source_span": "text that is not in the rule at all",
                "atoms": [],
            }
        ]
    }
    _, _, still, _ = compiler.apply_proposal(
        record, invented, rule.coverage, rule.exemptions, rule.unmapped_text
    )
    assert len(still) == 1, "a span that does not occur leaves the clause unmapped"


def test_a_set_aside_clause_is_kept_with_its_reason_and_stays_low_confidence():
    record = FakeRule(coverage_conditions="Tenancies under California law")
    rule, _ = compiler.compile_rule(record)
    assert not rule.has_unmapped
    assert rule.coverage_basis is CoverageBasis.classified
    assert rule.review_state is ReviewState.machine_classified  # never machine_verified
    [clause] = rule.classified
    assert clause.text == "Tenancies under California law" and clause.rationale
