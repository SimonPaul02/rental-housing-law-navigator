"""The pure evaluator: three-valued logic, time, coverage, interactions, export.

No database, no model, no clock. Every case here is one of the failure modes
the module is meant to make impossible, and most of them are cases where the
plausible shortcut gives a confidently wrong answer.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.modules.address_lookup.rule_adapter.models import (
    Atom,
    CompiledRule,
    CoverageBasis,
    EffectiveDate,
    Expr,
    Field,
    Op,
    Origin,
    Qualification,
    Relation,
    RelationType,
    ReviewState,
    SourceAnchor,
    UnmappedClause,
)
from app.modules.address_lookup.rule_evaluation.base import evaluate_base
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseResult,
    Ternary,
    conjunction,
    disjunction,
)
from app.modules.address_lookup.rule_evaluation.explanations import explain
from app.modules.address_lookup.rule_evaluation.export import (
    to_submission,
    validate_submission,
)
from app.modules.address_lookup.rule_evaluation.interactions import (
    find_cycles,
    resolve_interactions,
)
from app.modules.address_lookup.rule_evaluation.predicates import AddressEvidence, FactValue

AS_OF = dt.date(2026, 10, 1)
ANCHOR = SourceAnchor("quoted text from the ordinance")


def evidence(**kw) -> AddressEvidence:
    base = dict(
        address_id="A0001",
        legal_city=FactValue("San Francisco", "present"),
        legal_state=FactValue("CA", "present"),
        units=FactValue(32, "present"),
        year_built=FactValue(1927, "present"),
        certificate_of_occupancy_date=FactValue(None, "not_supplied"),
        use_code=FactValue(None, "not_supplied"),
        jurisdiction_method="census",
    )
    for key, value in kw.items():
        base[key] = value
    return AddressEvidence(**base)


def rule(**kw) -> CompiledRule:
    base = dict(
        team_rule_id="r-city",
        rule_version_hash="sha256:test",
        review_state=ReviewState.human_approved,
        coverage_basis=CoverageBasis.explicit_unconditional,
        level="city",
        jurisdiction="San Francisco, CA",
        status="in_force",
        coverage=Expr("all", ()),
        exemptions=Expr("any", ()),
        issue_key="rent_increase_cap",
    )
    base.update(kw)
    return CompiledRule(**base)


def atom(f: Field, op: Op, value=None, cid="c1") -> Atom:
    return Atom(id=cid, field=f, op=op, value=value, anchor=ANCHOR)


def decide(compiled, ev, as_of=AS_OF):
    base = evaluate_base(compiled, ev, as_of)
    final = resolve_interactions([base], [])[0]
    final.explanation = explain(final)
    return final


# -- three-valued logic ------------------------------------------------------
@pytest.mark.parametrize(
    "values,expected",
    [
        ([Ternary.true, Ternary.true], Ternary.true),
        ([Ternary.true, Ternary.unknown], Ternary.unknown),
        ([Ternary.unknown, Ternary.false], Ternary.false),
        ([Ternary.false, Ternary.false], Ternary.false),
    ],
)
def test_conjunction_lets_a_definite_false_beat_an_unknown(values, expected):
    assert conjunction(values) is expected


@pytest.mark.parametrize(
    "values,expected",
    [
        ([Ternary.false, Ternary.true], Ternary.true),
        ([Ternary.false, Ternary.unknown], Ternary.unknown),
        ([Ternary.false, Ternary.false], Ternary.false),
        ([Ternary.unknown, Ternary.true], Ternary.true),
    ],
)
def test_disjunction_lets_a_definite_true_beat_an_unknown(values, expected):
    assert disjunction(values) is expected


def test_a_ternary_cannot_be_used_as_a_boolean():
    """Guards against `if coverage:` quietly treating unknown as true."""
    with pytest.raises(TypeError):
        bool(Ternary.unknown)


# -- the exemption cases the challenge names ---------------------------------
def test_a_large_building_defeats_the_small_landlord_exemption():
    """Owner identity is absent from the data, and does not need to be: the
    exemption needs 2 or fewer units and the building has 32."""
    compiled = rule(
        exemptions=Expr(
            "any",
            (
                Expr(
                    "all",
                    (
                        atom(Field.owner_occupied, Op.is_true, cid="x1"),
                        atom(Field.units, Op.lte, 2, cid="x2"),
                    ),
                ),
            ),
        )
    )
    decision = decide(compiled, evidence(units=FactValue(32, "present")))
    assert decision.result is BaseResult.applies
    assert "exemption cannot apply" in decision.explanation


def test_a_two_unit_building_leaves_the_same_exemption_unknown():
    compiled = rule(
        exemptions=Expr(
            "any",
            (
                Expr(
                    "all",
                    (
                        atom(Field.owner_occupied, Op.is_true, cid="x1"),
                        atom(Field.units, Op.lte, 2, cid="x2"),
                    ),
                ),
            ),
        )
    )
    decision = decide(compiled, evidence(units=FactValue(2, "present")))
    assert decision.result is BaseResult.unknown
    assert "deliberately omits" in decision.explanation


def test_an_or_exemption_keeps_both_branches():
    """A 32-unit building kills the owner-occupied branch, but the seasonal
    branch is still unknown, so the answer must stay unknown."""
    compiled = rule(
        exemptions=Expr(
            "any",
            (
                Expr(
                    "all",
                    (
                        atom(Field.owner_occupied, Op.is_true, cid="x1"),
                        atom(Field.units, Op.lte, 2, cid="x2"),
                    ),
                ),
                atom(Field.seasonal_rental, Op.is_true, cid="x3"),
            ),
        )
    )
    decision = decide(compiled, evidence(units=FactValue(32, "present")))
    assert decision.result is BaseResult.unknown


# -- certificate of occupancy ------------------------------------------------
def test_the_cutoff_year_cannot_be_settled_by_year_built():
    compiled = rule(
        coverage=Expr(
            "all", (atom(Field.certificate_of_occupancy_date, Op.lte, dt.date(1979, 6, 13)),)
        )
    )
    decision = decide(compiled, evidence(year_built=FactValue(1979, "present")))
    assert decision.result is BaseResult.unknown
    assert "not the certificate" in decision.explanation
    assert decision.base.unresolved_fields == ["certificate_of_occupancy_date"]


@pytest.mark.parametrize(
    "built,expected", [(1952, BaseResult.applies), (2014, BaseResult.does_not_apply)]
)
def test_year_built_outside_the_cutoff_year_settles_it(built, expected):
    compiled = rule(
        coverage=Expr(
            "all", (atom(Field.certificate_of_occupancy_date, Op.lte, dt.date(1979, 6, 13)),)
        )
    )
    decision = decide(compiled, evidence(year_built=FactValue(built, "present")))
    assert decision.result is expected


def test_a_real_certificate_date_settles_the_cutoff_year():
    compiled = rule(
        coverage=Expr(
            "all", (atom(Field.certificate_of_occupancy_date, Op.lte, dt.date(1979, 6, 13)),)
        )
    )
    decision = decide(
        compiled,
        evidence(
            year_built=FactValue(1979, "present"),
            certificate_of_occupancy_date=FactValue(dt.date(1979, 3, 2), "present"),
        ),
    )
    assert decision.result is BaseResult.applies


# -- fact status and scope ---------------------------------------------------
@pytest.mark.parametrize("status", ["not_supplied", "invalid", "conflicted"])
def test_an_unusable_fact_is_never_a_numeric_pass_or_fail(status):
    compiled = rule(coverage=Expr("all", (atom(Field.units, Op.gte, 5),)))
    decision = decide(compiled, evidence(units=FactValue(15, status)))
    assert decision.result is BaseResult.unknown
    assert "units" in decision.base.unresolved_fields


def test_a_fact_about_the_wrong_thing_cannot_prove_a_predicate():
    compiled = rule(coverage=Expr("all", (atom(Field.units, Op.gte, 5),)))
    decision = decide(compiled, evidence(units=FactValue(9, "present", scope="parcel")))
    assert decision.result is BaseResult.unknown
    assert "parcel" in decision.explanation


# -- geography ---------------------------------------------------------------
def test_a_mailing_city_never_stands_in_for_an_unresolved_legal_city():
    compiled = rule(jurisdiction="Los Angeles, CA")
    decision = decide(compiled, evidence(legal_city=FactValue(None, "not_supplied")))
    assert decision.result is BaseResult.unknown
    assert "mailing city is not the legal city" in decision.explanation


def test_a_resolved_legal_city_matches_whatever_the_mail_says():
    compiled = rule(jurisdiction="Los Angeles, CA")
    decision = decide(compiled, evidence(legal_city=FactValue("Los Angeles", "present")))
    assert decision.result is BaseResult.applies


def test_another_citys_rule_does_not_apply():
    compiled = rule(jurisdiction="Hoboken, NJ")
    decision = decide(
        compiled,
        evidence(
            legal_city=FactValue("Jersey City", "present"), legal_state=FactValue("NJ", "present")
        ),
    )
    assert decision.result is BaseResult.does_not_apply


# -- time --------------------------------------------------------------------
def test_an_exact_future_date_is_not_yet_effective_then_applies():
    compiled = rule(
        status="not_yet_effective",
        effective_dates=(
            EffectiveDate("2027-07-01", dt.date(2027, 7, 1), dt.date(2027, 7, 1), "day"),
        ),
    )
    assert decide(compiled, evidence()).result is BaseResult.not_yet_effective
    assert decide(compiled, evidence(), dt.date(2027, 7, 2)).result is BaseResult.applies


def test_a_month_precision_date_is_unknown_inside_its_month():
    compiled = rule(
        effective_dates=(
            EffectiveDate("2026-10", dt.date(2026, 10, 1), dt.date(2026, 10, 31), "month"),
        ),
    )
    inside = decide(compiled, evidence(), dt.date(2026, 10, 15))
    assert inside.result is BaseResult.unknown
    assert "month precision" in inside.explanation
    assert decide(compiled, evidence(), dt.date(2026, 11, 1)).result is BaseResult.applies
    assert decide(compiled, evidence(), dt.date(2026, 9, 1)).result is BaseResult.not_yet_effective


def test_conflicting_dates_are_unknown_between_the_candidates_and_flagged():
    compiled = rule(
        effective_dates=(
            EffectiveDate("2026-07-01", dt.date(2026, 7, 1), dt.date(2026, 7, 1), "day"),
            EffectiveDate("2027-01-01", dt.date(2027, 1, 1), dt.date(2027, 1, 1), "day"),
        ),
    )
    between = decide(compiled, evidence(), dt.date(2026, 10, 1))
    assert between.result is BaseResult.unknown
    assert between.conflict_flag
    assert decide(compiled, evidence(), dt.date(2027, 6, 1)).result is BaseResult.applies
    assert decide(compiled, evidence(), dt.date(2026, 1, 1)).result is BaseResult.not_yet_effective


def test_a_missing_effective_date_is_not_january_the_first():
    compiled = rule(status="not_yet_effective", effective_dates=())
    decision = decide(compiled, evidence())
    assert decision.result is BaseResult.unknown
    assert "not January 1" in decision.explanation


def test_a_pending_bill_never_becomes_law_by_date_arithmetic():
    compiled = rule(status="pending")
    for day in (dt.date(2026, 10, 1), dt.date(2030, 1, 1)):
        decision = decide(compiled, evidence(), day)
        assert decision.result is BaseResult.pending


def test_a_failed_measure_is_reported_nowhere():
    compiled = rule(status="failed")
    decision = decide(compiled, evidence())
    assert decision.result is BaseResult.failed
    assert not decision.base.could_be_relevant


def test_a_pending_bill_still_drops_out_where_coverage_definitely_excludes():
    compiled = rule(status="pending", coverage=Expr("all", (atom(Field.units, Op.gte, 50),)))
    decision = decide(compiled, evidence(units=FactValue(32, "present")))
    assert decision.result is BaseResult.does_not_apply


# -- unmapped text -----------------------------------------------------------
def test_an_unmapped_clause_forces_unknown_rather_than_applies():
    """The defect this whole package exists to remove."""
    compiled = rule(
        review_state=ReviewState.needs_review,
        unmapped_text=(
            UnmappedClause(
                "older buildings in designated districts", Origin.coverage, "no pattern"
            ),
        ),
    )
    decision = decide(compiled, evidence())
    assert decision.result is BaseResult.unknown
    assert "could not be translated" in decision.explanation


def test_an_unmapped_clause_does_not_rescue_a_definite_exclusion():
    compiled = rule(
        review_state=ReviewState.needs_review,
        coverage=Expr("all", (atom(Field.units, Op.gte, 50),)),
        unmapped_text=(UnmappedClause("something else", Origin.coverage, "no pattern"),),
    )
    decision = decide(compiled, evidence(units=FactValue(32, "present")))
    assert decision.result is BaseResult.does_not_apply


def test_empty_coverage_needs_approval_to_mean_unconditional():
    unreviewed = rule(
        review_state=ReviewState.needs_review,
        coverage_basis=CoverageBasis.unresolved,
        coverage=Expr("all", ()),
    )
    assert decide(unreviewed, evidence()).result is BaseResult.unknown
    approved = rule(review_state=ReviewState.human_approved, coverage=Expr("all", ()))
    assert decide(approved, evidence()).result is BaseResult.applies


# -- interactions ------------------------------------------------------------
def city_and_state():
    city = rule(team_rule_id="r-city", level="city", jurisdiction="San Francisco, CA")
    state = rule(team_rule_id="r-state", level="state", jurisdiction="CA")
    return city, state


def relation(**kw) -> Relation:
    base = dict(
        left_rule_id="r-state",
        right_rule_id="r-city",
        issue_key="rent_increase_cap",
        relation=RelationType.yields_to,
        anchor=ANCHOR,
        review_state=ReviewState.approved,
        qualification=Qualification.confirmed,
    )
    base.update(kw)
    return Relation(**base)


def test_without_a_reviewed_relation_both_a_state_and_a_city_rule_apply():
    """No implicit "city wins" - that default is a guess."""
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)], []
    )
    assert {d.team_rule_id: d.result for d in decisions} == {
        "r-city": BaseResult.applies,
        "r-state": BaseResult.applies,
    }


def test_a_reviewed_relation_supersedes_the_yielding_rule():
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)], [relation()]
    )
    by_id = {d.team_rule_id: d for d in decisions}
    assert by_id["r-city"].result is BaseResult.applies
    assert by_id["r-state"].result is BaseResult.superseded
    assert by_id["r-state"].superseded_by == "r-city"


def test_an_unreviewed_relation_makes_precedence_unknown():
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)],
        [relation(review_state=ReviewState.needs_review)],
    )
    by_id = {d.team_rule_id: d for d in decisions}
    assert by_id["r-city"].result is BaseResult.applies
    assert by_id["r-state"].result is BaseResult.unknown
    assert by_id["r-state"].conflict_flag


def test_a_disproved_or_future_qualification_does_not_supersede():
    city, state = city_and_state()
    bases = [evaluate_base(city, evidence(), AS_OF), evaluate_base(state, evidence(), AS_OF)]
    for candidate in (
        relation(qualification=Qualification.excluded),
        relation(qualification=Qualification.confirmed, valid_from=dt.date(2027, 1, 1)),
    ):
        by_id = {d.team_rule_id: d for d in resolve_interactions(bases, [candidate])}
        assert by_id["r-state"].result is BaseResult.applies


def test_a_relation_for_a_different_issue_supersedes_nothing():
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)],
        [relation(issue_key="deposit_cap")],
    )
    assert all(d.result is BaseResult.applies for d in decisions)


def test_an_uncertain_governing_rule_makes_the_dependent_rule_uncertain():
    city = rule(
        team_rule_id="r-city",
        coverage=Expr(
            "all", (atom(Field.certificate_of_occupancy_date, Op.lte, dt.date(1979, 6, 13)),)
        ),
    )
    state = rule(team_rule_id="r-state", level="state", jurisdiction="CA")
    ev = evidence(year_built=FactValue(1979, "present"))
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)], [relation()]
    )
    by_id = {d.team_rule_id: d for d in decisions}
    assert by_id["r-city"].result is BaseResult.unknown
    assert by_id["r-state"].result is BaseResult.unknown
    by_id["r-state"].explanation = explain(by_id["r-state"])
    assert "itself unknown" in by_id["r-state"].explanation


def test_a_governing_rule_that_does_not_apply_leaves_the_other_alone():
    city = rule(team_rule_id="r-city", coverage=Expr("all", (atom(Field.units, Op.gte, 50),)))
    state = rule(team_rule_id="r-state", level="state", jurisdiction="CA")
    ev = evidence(units=FactValue(32, "present"))
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)], [relation()]
    )
    by_id = {d.team_rule_id: d for d in decisions}
    assert by_id["r-city"].result is BaseResult.does_not_apply
    assert by_id["r-state"].result is BaseResult.applies


def test_both_apply_preserves_both_results():
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)],
        [relation(relation=RelationType.both_apply)],
    )
    assert all(d.result is BaseResult.applies for d in decisions)


def test_possible_conflict_keeps_the_results_and_raises_the_flag():
    city, state = city_and_state()
    ev = evidence()
    decisions = resolve_interactions(
        [evaluate_base(city, ev, AS_OF), evaluate_base(state, ev, AS_OF)],
        [relation(relation=RelationType.possible_conflict, condition="both claim to govern")],
    )
    by_id = {d.team_rule_id: d for d in decisions}
    assert by_id["r-state"].result is BaseResult.applies
    assert by_id["r-state"].conflict_flag


def test_a_relation_cycle_is_detected_rather_than_broken_by_id():
    cycle = find_cycles(
        [
            relation(left_rule_id="r-a", right_rule_id="r-b"),
            relation(left_rule_id="r-b", right_rule_id="r-a"),
        ]
    )
    assert cycle, "two rules each yielding to the other must be reported"


# -- export ------------------------------------------------------------------
def submission_for(results: list[tuple[str, BaseResult]], ids=("A0001", "A0002")):
    decisions = []
    for rule_id, result in results:
        base = evaluate_base(rule(team_rule_id=rule_id), evidence(), AS_OF)
        base.result = result
        final = resolve_interactions([base], [])[0]
        final.result = result
        final.explanation = f"explanation for {rule_id}"
        decisions.append(final)
    return to_submission(decisions, list(ids), AS_OF)


def test_every_supplied_address_appears_even_with_nothing_to_report():
    payload = submission_for([], ids=("A0001", "A0002", "A0003"))
    assert sorted(payload["lookups"]) == ["A0001", "A0002", "A0003"]
    assert payload["lookups"]["A0002"] == []


@pytest.mark.parametrize(
    "internal,reported",
    [
        (BaseResult.applies, "applies"),
        (BaseResult.unknown, "unknown"),
        (BaseResult.superseded, "superseded"),
        (BaseResult.not_yet_effective, "not_yet_effective"),
        (BaseResult.pending, "pending"),
    ],
)
def test_each_internal_result_maps_to_its_submission_value(internal, reported):
    payload = submission_for([("r-1", internal)])
    assert payload["lookups"]["A0001"][0]["result"] == reported


@pytest.mark.parametrize("internal", [BaseResult.does_not_apply, BaseResult.failed])
def test_internal_only_results_never_reach_the_file(internal):
    payload = submission_for([("r-1", internal)])
    assert payload["lookups"]["A0001"] == []


def test_the_validator_accepts_a_good_file():
    payload = submission_for([("r-1", BaseResult.applies)])
    assert (
        validate_submission(
            payload, expected_address_ids=["A0001", "A0002"], known_rule_ids={"r-1"}
        )
        == []
    )


def test_the_validator_catches_a_missing_address():
    payload = submission_for([("r-1", BaseResult.applies)], ids=("A0001",))
    problems = validate_submission(
        payload, expected_address_ids=["A0001", "A0002"], known_rule_ids={"r-1"}
    )
    assert any("absent" in p for p in problems)


def test_the_validator_catches_an_unknown_rule_id():
    payload = submission_for([("r-ghost", BaseResult.applies)])
    problems = validate_submission(
        payload, expected_address_ids=["A0001", "A0002"], known_rule_ids={"r-1"}
    )
    assert any("not in this run" in p for p in problems)


def test_the_validator_catches_an_empty_explanation():
    payload = submission_for([("r-1", BaseResult.applies)])
    payload["lookups"]["A0001"][0]["explanation"] = "  "
    problems = validate_submission(
        payload, expected_address_ids=["A0001", "A0002"], known_rule_ids={"r-1"}
    )
    assert any("explanation is empty" in p for p in problems)


def test_the_export_is_deterministic():
    first = submission_for([("r-2", BaseResult.applies), ("r-1", BaseResult.unknown)])
    second = submission_for([("r-1", BaseResult.unknown), ("r-2", BaseResult.applies)])
    assert first == second
    assert [e["team_rule_id"] for e in first["lookups"]["A0001"]] == ["r-1", "r-2"]


# -- explanations ------------------------------------------------------------
def test_an_explanation_names_the_decisive_fact():
    compiled = rule(coverage=Expr("all", (atom(Field.units, Op.gte, 5),)))
    decision = decide(compiled, evidence(units=FactValue(None, "not_supplied")))
    assert "units" in decision.explanation
    assert "Supplying units would settle it." in decision.explanation


def test_an_explanation_never_claims_a_reason_that_was_not_checked():
    compiled = rule(status="pending")
    decision = decide(compiled, evidence())
    assert "pending" in decision.explanation.lower()
    assert "certificate" not in decision.explanation.lower()
