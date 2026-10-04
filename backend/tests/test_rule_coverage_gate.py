"""Source and review gates for coverage that could otherwise produce applies."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from app.modules.address_lookup.rule_adapter.compiler import apply_proposal, compile_rule
from app.modules.address_lookup.rule_adapter.models import (
    Basis,
    CoverageBasis,
    Field,
    InvalidCompilation,
    Op,
    ReviewState,
    rule_version_hash,
)
from app.modules.address_lookup.rule_adapter.review import ReviewStore
from app.modules.address_lookup.rule_evaluation.base import evaluate_base
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult
from app.modules.address_lookup.rule_evaluation.predicates import AddressEvidence, FactValue


@dataclass
class Rule:
    team_rule_id: str = "r-test"
    title: str = "Test"
    requirement: str = "Test requirement"
    jurisdiction: str = "Los Angeles, CA"
    level: str = "city"
    category: str = "rent_increase_limits"
    status: str = "in_force"
    coverage_conditions: str | None = None
    exemptions: str | None = None
    source_doc_id: str = "D001"
    source_url: str = "https://example.gov/law"
    effective_date: str | None = None
    interaction: str | None = None
    overrides: list[str] | None = None
    citation: str = "Code § 1"
    quoted_span: str = "Test quoted span"
    key_value: str | None = None


def test_exact_simple_threshold_is_machine_verified():
    text = "5 or more units"
    compiled, _ = compile_rule(Rule(coverage_conditions=text), source_text=text)
    atom = next(compiled.coverage.atoms())
    assert (atom.field, atom.op, atom.value) == (Field.units, Op.gte, 5)
    assert atom.anchor.clause_id == "c1"
    assert atom.anchor.source_doc_id == "D001"
    assert atom.anchor.source_hash == compiled.source_hash
    assert compiled.review_state is ReviewState.machine_verified
    assert compiled.coverage_basis is CoverageBasis.conditions


@pytest.mark.parametrize(
    "source",
    [
        "6 or more units",
        "5 or fewer units",
        "not 5 or more units",
        "Either 5 or more units or covered by the RSO",
        "The rule mentions older vintage, not 5 or more units",
        "5 or more units; except properties outside the RSO",
    ],
)
def test_a_source_that_does_not_fully_support_the_clause_stays_pending(source):
    compiled, _ = compile_rule(Rule(coverage_conditions="5 or more units"), source_text=source)
    assert compiled.coverage.is_empty
    assert compiled.review_state is ReviewState.needs_review
    assert compiled.unmapped_text[0].clause_id == "c1"


def test_record_only_phrase_cannot_validate_a_condition():
    rule = Rule(coverage_conditions="5 or more units", requirement="5 or more units")
    compiled, _ = compile_rule(rule, source_text="The ordinance governs rental housing.")
    assert compiled.coverage.is_empty
    assert compiled.has_unmapped


@pytest.mark.parametrize(
    "field,op,value,passage",
    [
        ("units", "gte", 6, "5 or more units"),
        ("units", "lte", 5, "5 or more units"),
        (
            "certificate_of_occupancy_date",
            "lte",
            "1979-06-13",
            "certificate of occupancy issued on or before 1980-06-13",
        ),
        ("units", "gte", 5, "2 or more units"),
    ],
)
def test_ai_atom_must_match_entire_correct_clause(field, op, value, passage):
    clause = "Older vintage" if passage != "2 or more units" else "Older vintage"
    source = f"{clause}. {passage}"
    record = Rule(coverage_conditions=clause)
    compiled, _ = compile_rule(record, source_text=source)
    proposal = {
        "clauses": [
            {
                "text": clause,
                "verdict": "condition",
                "source_span": passage,
                "atoms": [
                    {"id": "p1", "field": field, "op": op, "value": value, "source_span": passage}
                ],
            }
        ]
    }
    coverage, _, pending, _ = apply_proposal(
        record,
        proposal,
        compiled.coverage,
        compiled.exemptions,
        compiled.unmapped_text,
        source_text=source,
    )
    assert coverage.is_empty
    assert len(pending) == 1


def test_verified_clause_is_kept_and_rso_clause_is_read_as_membership():
    coverage = "5 or more units; units subject to the Los Angeles RSO"
    source = "5 or more units. units subject to the Los Angeles RSO"
    compiled, _ = compile_rule(Rule(coverage_conditions=coverage), source_text=source)
    verified, membership = compiled.coverage.atoms()
    assert (verified.op, verified.value, verified.basis) == (Op.gte, 5, Basis.source_verified)
    assert (membership.field, membership.basis) == (
        Field.los_angeles_rso_membership,
        Basis.machine_read,
    )
    assert compiled.review_state is ReviewState.machine_classified


@pytest.mark.parametrize(
    "coverage,exemption",
    [
        ("Units subject to the Los Angeles RSO", None),
        ("Rental units in Los Angeles", "Non-RSO properties are not covered"),
    ],
)
def test_rso_cross_reference_stays_material(coverage, exemption):
    """Being in Los Angeles never satisfies RSO membership: it stays a
    condition on the building, read from year built, never unconditional."""
    source = "\n".join(x for x in (coverage, exemption) if x)
    compiled, _ = compile_rule(
        Rule(coverage_conditions=coverage, exemptions=exemption), source_text=source
    )
    assert compiled.coverage_basis is not CoverageBasis.explicit_unconditional
    fields = {a.field for a in (*compiled.coverage.atoms(), *compiled.exemptions.atoms())}
    assert Field.los_angeles_rso_membership in fields
    no_year = AddressEvidence(
        address_id="A0001",
        legal_city=FactValue("Los Angeles", "present"),
        legal_state=FactValue("CA", "present"),
        units=FactValue(32, "present"),
        year_built=FactValue(),
        certificate_of_occupancy_date=FactValue(),
        use_code=FactValue(),
    )
    assert evaluate_base(compiled, no_year, dt.date(2026, 10, 4)).result is BaseResult.unknown


def test_missing_coverage_does_not_become_unconditional():
    compiled, _ = compile_rule(Rule(), source_text="This rule governs residential rentals.")
    assert compiled.coverage_basis is CoverageBasis.unresolved
    assert compiled.review_state is ReviewState.needs_review


def test_rule_hash_includes_title_requirement_and_source_identity():
    original = rule_version_hash(Rule())
    assert rule_version_hash(Rule(title="Different")) != original
    assert rule_version_hash(Rule(requirement="Different")) != original
    assert rule_version_hash(Rule(source_doc_id="D002")) != original
    assert rule_version_hash(Rule(source_url="https://example.gov/other")) != original


def test_legacy_automatic_approval_is_not_loaded_as_a_usable_revision(tmp_path):
    compiled, _ = compile_rule(
        Rule(coverage_conditions="All residential rental units"),
        source_text="All residential rental units",
    )
    store = ReviewStore(tmp_path / "reviews.json")
    old = compiled.to_json()
    old["compiler_version"] = "1"
    old["review_state"] = "approved"
    store.revisions[compiled.team_rule_id] = old
    assert (
        store.get(compiled.team_rule_id, compiled.rule_version_hash, compiled.source_hash) is None
    )


def test_a_note_that_only_flips_review_state_does_not_approve(tmp_path):
    source = "All residential rental units"
    compiled, _ = compile_rule(Rule(coverage_conditions=source), source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    payload = compiled.to_json()
    payload["review_state"] = "human_approved"
    store.revisions[compiled.team_rule_id] = payload
    store.reviews[compiled.team_rule_id] = {
        "rule_version_hash": compiled.rule_version_hash,
        "source_hash": compiled.source_hash,
        "compiler_version": compiled.compiler_version,
        "review_state": "human_approved",
        "note": "approved",
    }
    loaded = store.get(compiled.team_rule_id, compiled.rule_version_hash, compiled.source_hash)
    assert loaded.review_state is ReviewState.needs_review
    assert loaded.coverage_basis is CoverageBasis.unresolved


def test_human_clearance_is_bound_to_current_rule_source_and_compiler(tmp_path):
    source = "Rental dwelling units offered by a housing provider"
    record = Rule(coverage_conditions=source)
    compiled, _ = compile_rule(record, source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    store.review_coverage(
        compiled,
        source_text=source,
        reviewer="Alex",
        rationale="Reviewed the program scope",
        resolved_clause_ids=["c1"],
        coverage_basis="explicit_unconditional",
        scope_evidence_span=source,
    )
    with pytest.raises(InvalidCompilation, match="current source passage"):
        store.review_coverage(
            compiled,
            source_text=source,
            reviewer="Alex",
            rationale="No source passage supplied",
            resolved_clause_ids=["c1"],
            coverage_basis="explicit_unconditional",
        )
    accepted = store.apply_reviews(compiled)
    assert accepted.review_state is ReviewState.human_approved
    assert accepted.coverage_basis is CoverageBasis.explicit_unconditional
    assert not accepted.has_unmapped

    changed, _ = compile_rule(
        Rule(coverage_conditions=source, title="Changed title"), source_text=source
    )
    # The stale review no longer applies; only the machine's own reading is left.
    assert store.apply_reviews(changed).review_state is ReviewState.machine_classified
    with pytest.raises(InvalidCompilation, match="source or compiler version changed"):
        store.review_coverage(
            changed,
            source_text="changed source",
            reviewer="Alex",
            rationale="reason",
            resolved_clause_ids=["c1"],
            coverage_basis="explicit_unconditional",
        )


def test_rso_scope_cannot_be_reviewed_as_unconditional(tmp_path):
    source = "Units subject to the Los Angeles RSO"
    compiled, _ = compile_rule(Rule(coverage_conditions=source), source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    with pytest.raises(InvalidCompilation, match="RSO scope"):
        store.review_coverage(
            compiled,
            source_text=source,
            reviewer="Alex",
            rationale="Incorrectly treating Los Angeles as proof of membership",
            resolved_clause_ids=["c1"],
            coverage_basis="explicit_unconditional",
        )


def test_review_rejects_an_invalid_corrected_expression(tmp_path):
    source = "Units subject to the Los Angeles RSO"
    compiled, _ = compile_rule(Rule(coverage_conditions=source), source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    with pytest.raises(InvalidCompilation, match="source span"):
        store.review_coverage(
            compiled,
            source_text=source,
            reviewer="Alex",
            rationale="Correction",
            resolved_clause_ids=["c1"],
            coverage_basis="conditions",
            coverage={"all": [{"id": "c1.1", "field": "units", "op": "gte", "value": 5}]},
        )


def test_reviewed_rso_membership_is_read_from_year_built_at_low_confidence(tmp_path):
    source = "Units subject to the Los Angeles RSO"
    compiled, _ = compile_rule(Rule(coverage_conditions=source), source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    store.review_coverage(
        compiled,
        source_text=source,
        reviewer="Alex",
        rationale="RSO membership is the material coverage condition",
        resolved_clause_ids=["c1"],
        coverage_basis="conditions",
        coverage={
            "all": [
                {
                    "id": "c1.1",
                    "field": "los_angeles_rso_membership",
                    "op": "is_true",
                    "source_span": source,
                    "source_doc_id": "D001",
                    "source_hash": compiled.source_hash,
                    "clause_id": "c1",
                    "origin": "coverage",
                }
            ]
        },
    )
    reviewed = store.apply_reviews(compiled)
    assert reviewed.review_state is ReviewState.human_approved

    def built(year):
        return AddressEvidence(
            address_id="A0001",
            legal_city=FactValue("Los Angeles", "present"),
            legal_state=FactValue("CA", "present"),
            units=FactValue(32, "present"),
            year_built=FactValue(year, "present" if year else "not_supplied"),
            certificate_of_occupancy_date=FactValue(),
            use_code=FactValue(),
        )

    # Before the RSO's 1978-10-01 cutoff year: inside, on the proxy's word.
    decision = evaluate_base(reviewed, built(1927), dt.date(2026, 10, 4))
    assert decision.result is BaseResult.applies
    assert any(c.basis == "proxy" for c in decision.checks)
    # In the cutoff year, or with no year at all, the address still decides nothing.
    for year in (1978, None):
        decision = evaluate_base(reviewed, built(year), dt.date(2026, 10, 4))
        assert decision.result is BaseResult.unknown
    assert evaluate_base(reviewed, built(1995), dt.date(2026, 10, 4)).result is (
        BaseResult.does_not_apply
    )


def test_human_can_hold_or_reject_without_approving_coverage(tmp_path):
    source = "Units subject to the Los Angeles RSO"
    compiled, _ = compile_rule(Rule(coverage_conditions=source), source_text=source)
    store = ReviewStore(tmp_path / "reviews.json")
    store.review_coverage(
        compiled,
        source_text=source,
        reviewer="Alex",
        rationale="Need RSO records",
        resolved_clause_ids=[],
        decision="hold",
    )
    assert store.apply_reviews(compiled).review_state is ReviewState.needs_review
    store.review_coverage(
        compiled,
        source_text=source,
        reviewer="Alex",
        rationale="Wrong interpretation",
        resolved_clause_ids=[],
        decision="reject",
    )
    assert store.apply_reviews(compiled).review_state is ReviewState.rejected
