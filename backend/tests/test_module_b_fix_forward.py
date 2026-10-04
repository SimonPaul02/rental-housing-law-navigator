"""Module B after the fix-forward: outcomes, confidence, dates, coverage.

Built on real Rule and Address rows with no database, like
test_rule_evaluation.py, so the path under test is the one the API takes.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.models import Address, AddressJurisdiction, Document, Rule
from app.modules.address_lookup.rule_adapter.classify import STRUCTURED_COVERAGE, classify
from app.modules.address_lookup.rule_adapter.compiler import (
    candidate_dates,
    compile_rule,
    enactment_offset,
    parse_effective_date,
    split_clauses,
)
from app.modules.address_lookup.rule_adapter.models import (
    Atom,
    Basis,
    ClassifiedClause,
    CompiledRule,
    CoverageBasis,
    Expr,
    Field,
    InvalidCompilation,
    Op,
    Origin,
    ReviewState,
    SourceAnchor,
)
from app.modules.address_lookup.rule_adapter.review import ReviewStore, compiled_from_json
from app.modules.address_lookup.rule_evaluation.base import evaluate_base
from app.modules.address_lookup.rule_evaluation.confidence import assess
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseResult,
    Confidence,
    Decision,
    Reason,
    Ternary,
)
from app.modules.address_lookup.rule_evaluation.derived_facts import derive
from app.modules.address_lookup.rule_evaluation.explanations import explain
from app.modules.address_lookup.rule_evaluation.export import to_submission, validate_submission
from app.modules.address_lookup.rule_evaluation.predicates import (
    _OPS,
    AddressEvidence,
    FactValue,
    evaluate_atom,
)
from app.modules.address_lookup.rule_evaluation.timing import evaluate_time
from app.modules.address_lookup.service import (
    _outcome_from,
    _reported_outcomes,
    clear_compiled_cache,
    compiled_for,
    decide_for_address,
    evidence_for,
)

AS_OF = dt.date(2026, 10, 1)


def setup_function() -> None:
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
        use_code="0500",
        use_description="Five or more apartments",
    )
    defaults.update(kw)
    legal_city = defaults.pop("legal_city", defaults["postal_city"])
    address = Address(**defaults)
    address.jurisdiction = AddressJurisdiction(
        address_id=address.address_id,
        legal_city=legal_city,
        legal_state=address.state,
        method="census",
    )
    return address


def make_rule(team_rule_id: str = "r-0001", body: str | None = None, **kw) -> Rule:
    defaults = dict(
        team_rule_id=team_rule_id,
        jurisdiction="CA",
        level="state",
        category="algorithmic_rent_setting",
        status="in_force",
        title="Test rule",
        requirement="Does a thing.",
        citation="Cal. Civ. Code § 1",
        source_url=f"https://example.gov/{team_rule_id}",
        quoted_span="x" * 25,
        coverage_conditions="Residential rentals statewide",
        source_doc_id=f"D-{team_rule_id}",
        overrides=[],
        conflict_flag=False,
    )
    defaults.update(kw)
    rule = Rule(**defaults)
    rule.document = Document(
        doc_id=rule.source_doc_id,
        jurisdictions=rule.jurisdiction,
        url=rule.source_url,
        body=body if body is not None else f"{rule.coverage_conditions}.",
    )
    return rule


def decide_all(rules: list[Rule], address: Address, as_of: dt.date = AS_OF):
    compiled = {r.team_rule_id: compiled_for(r) for r in rules}
    decisions = decide_for_address(rules, compiled, [], evidence_for(address), as_of)
    return compiled, {d.team_rule_id: d for d in decisions}


# -- outcomes ----------------------------------------------------------------
def test_not_applicable_outcomes_can_be_listed_without_crashing():
    here, elsewhere = make_rule("r-ca"), make_rule("r-nj", jurisdiction="NJ")
    compiled, decisions = decide_all([here, elsewhere], make_address())
    assert decisions["r-nj"].result is BaseResult.does_not_apply

    reportable, shown = _reported_outcomes(
        list(decisions.values()), [here, elsewhere], compiled, include_not_applicable=True
    )
    assert [o.team_rule_id for o in reportable] == ["r-ca"]
    assert {o.team_rule_id for o in shown} == {"r-ca", "r-nj"}


def test_module_a_conflict_flag_reaches_api_and_export_alike():
    rule = make_rule(conflict_flag=True, conflict_note="two published effective dates")
    compiled, decisions = decide_all([rule], make_address())
    decision = decisions["r-0001"]
    outcome = _outcome_from(decision, rule, compiled["r-0001"])
    payload = to_submission([decision], ["A0001"], AS_OF)
    exported = payload["lookups"]["A0001"][0]

    assert decision.conflict_flag and outcome.conflict_flag and exported["conflict_flag"]
    assert "two published effective dates" in exported["explanation"]
    assert not validate_submission(
        payload, expected_address_ids=["A0001"], known_rule_ids={"r-0001"}
    )


def test_boolean_operators_compare_supplied_booleans():
    assert _OPS[Op.is_true](True, None) and not _OPS[Op.is_true](False, None)
    assert _OPS[Op.is_false](False, None) and not _OPS[Op.is_false](None, None)


# -- confidence --------------------------------------------------------------
def _presumed_rule() -> CompiledRule:
    anchor = SourceAnchor("4 or fewer units", "D1", origin=Origin.exemption)
    return CompiledRule(
        team_rule_id="r-low",
        rule_version_hash="h",
        review_state=ReviewState.machine_classified,
        coverage_basis=CoverageBasis.classified,
        jurisdiction="CA",
        exemptions=Expr(
            "any", (Atom("x1.1", Field.units, Op.lte, 4, anchor, basis=Basis.presumed),)
        ),
        classified=(
            ClassifiedClause("Landlords", Origin.coverage, "c1", "actor", "names who, not which"),
        ),
        unverified_dates=(parse_effective_date("2024-01-01"),),
    )


def test_new_compiled_fields_round_trip_and_stale_classifier_is_recompiled(tmp_path):
    rule = _presumed_rule()
    payload = rule.to_json()
    assert payload["exemptions"]["any"][0]["basis"] == "presumed"
    assert compiled_from_json(payload).to_json() == payload

    store = ReviewStore(tmp_path / "compiled.json")
    store.put(rule)
    assert store.get("r-low", "h", None) is not None
    store.revisions["r-low"].pop("classifier_version")
    assert store.get("r-low", "h", None) is None


def test_a_presumed_fact_makes_the_answer_low_confidence_and_says_why():
    rule = _presumed_rule()
    base = evaluate_base(rule, evidence_for(make_address()), AS_OF)
    decision = Decision(base=base, result=base.result)
    assess(decision, rule)
    decision.explanation = explain(decision)

    assert decision.result is BaseResult.applies
    assert decision.confidence is Confidence.low
    assert any("presumed" in r for r in decision.confidence_reasons)
    assert any("no building-level condition" in r for r in decision.confidence_reasons)
    assert "Confidence: low" in decision.explanation
    assert decision.to_json()["confidence"] == "low"

    payload = to_submission([decision], ["A0001"], AS_OF)
    assert set(payload["lookups"]["A0001"][0]) == {
        "team_rule_id",
        "result",
        "explanation",
        "conflict_flag",
    }


def test_a_source_backed_answer_stays_high_confidence():
    rule = make_rule()
    compiled, decisions = decide_all([rule], make_address())
    outcome = _outcome_from(decisions["r-0001"], rule, compiled["r-0001"])
    assert outcome.result == "applies" and outcome.confidence == "high"
    assert "Confidence" not in outcome.explanation


# -- dates -------------------------------------------------------------------
def corpus(doc_id: str) -> str:
    return (settings.corpus_dir / "text" / f"{doc_id}.txt").read_text(encoding="utf-8")


def dated(effective_date: str | None, **kw) -> SimpleNamespace:
    return SimpleNamespace(
        effective_date=effective_date,
        interaction=None,
        requirement="",
        key_value=kw.get("key_value"),
    )


def test_fair_act_date_is_computed_from_its_own_enactment_clause():
    found, notes, unresolved, _, unverified = candidate_dates(dated("2027-07-01"), corpus("D069"))
    assert [d.raw for d in found] == ["2027-07-01"] and not unresolved and not unverified
    assert any("twelfth month" in n and "July 20, 2026" in n for n in notes)

    rule = CompiledRule(
        team_rule_id="nj-fair",
        rule_version_hash="h",
        status="not_yet_effective",
        effective_dates=tuple(found),
    )
    assert evaluate_time(rule, dt.date(2026, 10, 1))[1] is BaseResult.not_yet_effective
    assert evaluate_time(rule, dt.date(2027, 7, 2))[0] is Ternary.true


def test_other_new_jersey_acts_compute_their_dates_too():
    assert enactment_offset(corpus("D065")).date == dt.date(2022, 1, 1)
    assert enactment_offset(corpus("D066")).date == dt.date(2026, 5, 1)
    assert enactment_offset("This act shall take effect immediately.") is None  # no enactment


def test_a_record_date_the_computation_contradicts_is_a_conflict():
    found, _, unresolved, _, unverified = candidate_dates(dated("2027-07-20"), corpus("D069"))
    assert [d.raw for d in found] == ["2027-07-01"] and [d.raw for d in unverified] == [
        "2027-07-20"
    ]
    rule = CompiledRule(
        team_rule_id="x",
        rule_version_hash="h",
        status="not_yet_effective",
        effective_dates=tuple(found),
        effective_date_unresolved=unresolved,
        unverified_dates=tuple(unverified),
    )
    _, result, trace = evaluate_time(rule, dt.date(2027, 7, 10))
    assert result is BaseResult.unknown and trace.reason is Reason.conflicting_effective_dates


def test_went_into_effect_and_not_effective_until_start_a_law_but_effective_until_ends_one():
    assert candidate_dates(dated("2024-10-14"), corpus("D081"))[0][0].raw == "2024-10-14"
    body = "The new ordinances are not effective until November 19, 2021."
    assert candidate_dates(dated("2021-11-19"), body)[0][0].raw == "2021-11-19"
    # D052 marks the old wording "effective until August 1, 2025" - an end, not a start.
    ended = "[Introductory paragraph of clause (b) effective until August 1, 2025.]"
    found, _, unresolved, _, unverified = candidate_dates(dated("2025-08-01"), ended)
    assert not found and unresolved and [d.raw for d in unverified] == ["2025-08-01"]


def test_a_deposit_interest_rate_period_is_not_a_law_date():
    _, notes, unresolved, period, unverified = candidate_dates(
        dated("2026-03-01", key_value="4.2%"), corpus("D083")
    )
    assert period and period.start == dt.date(2026, 3, 1) and not unresolved and not unverified


def test_an_unverified_date_is_read_as_a_date_at_low_confidence():
    future = CompiledRule(
        team_rule_id="x",
        rule_version_hash="h",
        status="not_yet_effective",
        effective_date_unresolved=True,
        unverified_dates=(parse_effective_date("2027-07-01"),),
    )
    in_force, result, trace = evaluate_time(future, dt.date(2026, 10, 1))
    assert result is BaseResult.not_yet_effective and trace.basis == "unverified_date"
    assert evaluate_time(future, dt.date(2027, 7, 2))[0] is Ternary.true

    # Recorded in force, but the date is later than the query: maybe an
    # amendment, maybe the start - unknown, not "not yet effective".
    recorded = replace(future, status="in_force")
    in_force, result, trace = evaluate_time(recorded, dt.date(2026, 10, 1))
    assert result is BaseResult.unknown and trace.reason is Reason.effective_date_unverified


AB325_SUMMARY = (
    "2025 legislative summary ... AB 325 (Aguiar-Curry), Ch. 338 Cartwright Act: "
    "common pricing algorithms. Effective date: January 1, 2026. ..."
)


def test_a_date_may_be_quoted_from_another_document_naming_the_same_act(tmp_path):
    bill = "Bill Text - AB-325 Cartwright Act. CHAPTER 338. AB 325, Aguiar-Curry. Approved."
    record = make_rule("r-ab325", body=bill, effective_date="2026-01-01")
    compiled = compile_rule(record, source_text=bill)[0]
    assert compiled.effective_date_unresolved  # D022 alone never states it

    store = ReviewStore(tmp_path / "compiled.json")
    span = (
        "AB 325 (Aguiar-Curry), Ch. 338 Cartwright Act: common pricing algorithms. "
        "Effective date: January 1, 2026"
    )
    review = dict(
        source_text=bill,
        reviewer="source-check",
        rationale="The courts' 2025 summary states the chaptered act's effective date.",
        effective_dates=[{"raw": "2026-01-01", "source_span": span}],
        evidence_doc_id="D092",
        evidence_text=AB325_SUMMARY,
    )
    with pytest.raises(InvalidCompilation, match="does not name"):
        store.review_dates(compiled, **review, act_markers=("AB 325", "Ch. 339"))
    with pytest.raises(InvalidCompilation, match="same act"):
        store.review_dates(compiled, **review, act_markers=("AB 325", "SB 763"))
    store.review_dates(compiled, **review, act_markers=("AB 325", "338"))

    reviewed = store.apply_date_review(compiled)
    assert [d.raw for d in reviewed.effective_dates] == ["2026-01-01"]
    assert not reviewed.unverified_dates
    assert evaluate_time(reviewed, dt.date(2025, 12, 31))[1] is BaseResult.not_yet_effective
    assert store.date_reviews["r-ab325"]["evidence_doc_id"] == "D092"


# -- facts read from the parcel record ----------------------------------------
def parcel(description: str, state: str = "CA", use_code: str = "", **kw) -> AddressEvidence:
    base = AddressEvidence(
        address_id="A1",
        legal_city=FactValue("X", "present"),
        legal_state=FactValue(state, "present"),
        units=kw.get("units", FactValue()),
        year_built=kw.get("year_built", FactValue()),
        certificate_of_occupancy_date=FactValue(),
        use_code=FactValue(use_code, "present" if use_code else "not_supplied"),
        use_description=FactValue(description, "present"),
    )
    return derive(base, state)


def test_the_parcel_description_states_or_implies_facts_and_says_which():
    apartments = parcel("Five or more apartments")
    assert apartments.property_type.value == "apartment_building"
    assert apartments.property_type.basis == "presumed"
    assert apartments.units_floor.value == 5
    assert apartments.building_is_subsidised.value is False
    assert apartments.building_is_subsidised.basis == "presumed"

    section8 = parcel("SUBSD HOUSING S- 8", "MA", "A/125")
    assert section8.building_is_subsidised.value is True
    assert section8.building_is_subsidised.basis is None  # stated, not presumed

    assert parcel("3S-F-D-6U-NH", "NJ", "4C").units_floor.value == 6
    assert parcel("2F-4U/2F-2U", "NJ", "4C").units_floor.value == 2  # smallest building
    assert parcel("2SF2UG", "NJ", "4C").units_floor.value == 5  # garage code, class 4C floor
    assert parcel("APT 7-30 UNITS", "MA", "A/112").units_floor.value == 7
    assert not parcel("TIC Bldg 4 units or less").property_type.usable


def test_a_presumption_can_rule_out_an_exemption_but_never_decide_coverage():
    evidence = parcel("Five or more apartments")
    subsidised = Atom("x1.1", Field.building_is_subsidised, Op.is_true, None, SourceAnchor("s"))
    exempt = evaluate_atom(subsidised, evidence, role=Origin.exemption)
    assert exempt.value is Ternary.false and exempt.basis == "presumed"
    assert evaluate_atom(subsidised, evidence, role=Origin.coverage).value is Ternary.unknown
    # Nor may it bring a building *inside* an exemption.
    unsubsidised = replace(subsidised, op=Op.is_false)
    assert evaluate_atom(unsubsidised, evidence, role=Origin.exemption).value is Ternary.unknown

    small = Atom("x1.2", Field.units, Op.lte, 4, SourceAnchor("4 or fewer units"))
    assert evaluate_atom(small, evidence, role=Origin.exemption).value is Ternary.false
    assert evaluate_atom(small, evidence, role=Origin.coverage).value is Ternary.unknown


def test_an_owner_holds_at_least_the_building_whoever_owns_it():
    portfolio = Atom("x1.1", Field.owner_unit_count, Op.lte, 4, SourceAnchor("four units total"))
    stated = parcel("", units=FactValue(32, "present"))
    check = evaluate_atom(portfolio, stated, role=Origin.coverage)
    assert check.value is Ternary.false and check.basis is None
    assert evaluate_atom(portfolio, parcel("")).value is Ternary.unknown


def test_the_rolling_fifteen_year_test_is_unknown_only_when_the_line_falls_in_the_year():
    recent = Atom(
        "x1.1",
        Field.years_since_certificate_of_occupancy,
        Op.lt,
        15,
        SourceAnchor("certificate of occupancy within the previous 15 years"),
    )

    def built(year: int) -> Ternary:
        evidence = parcel("", year_built=FactValue(year, "present"))
        return evaluate_atom(recent, evidence, as_of=AS_OF).value

    assert built(2015) is Ternary.true  # the line is 2011-10-01
    assert built(2011) is Ternary.unknown
    assert built(2000) is Ternary.false


# -- splitting and classifying clauses -----------------------------------------
FIXTURE = Path(__file__).parent / "fixtures" / "clause_readings.json"


def _reading_json(reading) -> dict:
    if reading is None:
        return {"kind": None}
    out = {"kind": "unresolved" if reading.unresolved else reading.kind}
    if reading.atoms:
        out["atoms"] = [
            [str(f), str(o), v.isoformat() if isinstance(v, dt.date) else v]
            for f, o, v in reading.atoms
        ]
        if reading.alternatives:
            out["alternatives"] = True
        if reading.as_exemption:
            out["as_exemption"] = True
    return out


def test_every_real_clause_reads_as_reviewed():
    """The 115-rule snapshot, clause by clause. A failure here is a change in
    what the system says about real law - review it, then regenerate."""
    rules = json.loads(FIXTURE.read_text(encoding="utf-8"))["rules"]
    for rule in rules:
        record = SimpleNamespace(**rule)
        got = []
        for origin, text in (
            (Origin.coverage, rule["coverage_conditions"]),
            (Origin.exemption, rule["exemptions"]),
        ):
            if not isinstance(text, str) or not text.strip():
                continue
            if origin is Origin.coverage and STRUCTURED_COVERAGE.search(text):
                got.append({"origin": "coverage", "clause": None, "kind": "structured"})
                continue
            for clause in split_clauses(text, alternatives=origin is Origin.exemption):
                reading = _reading_json(classify(clause, origin, record))
                got.append({"origin": origin.value, "clause": clause, **reading})
        assert got == rule["readings"], rule["team_rule_id"]


@pytest.mark.parametrize(
    "text,alternatives,parts",
    [
        ("Applies to any 'person' (as defined in Bus. & Prof. Code Section 16700)", True, 1),
        ("Advance payment when the lease term is six months or longer", True, 1),
        ("units where the tenant shares a bathroom or kitchen with an owner-occupant", True, 1),
        ("tenancies of 100 days or less (subsec. 9); Hotels", True, 2),
        ("exempt if not owned by a trust, corporation, or LLC with a corporate member", True, 1),
        ("Transient hotels; dormitories. Owner-occupied duplexes or K-12 schools", True, 4),
        (
            "Residential rental property; buildings with 5 or more units, and built before 1980",
            False,
            3,
        ),
    ],
)
def test_clauses_split_where_the_list_does_and_nowhere_else(text, alternatives, parts):
    assert len(split_clauses(text, alternatives=alternatives)) == parts


CA = SimpleNamespace(level="state", jurisdiction="CA")
NJ = SimpleNamespace(level="state", jurisdiction="NJ")
LA = SimpleNamespace(level="city", jurisdiction="Los Angeles, CA")
BERKELEY = SimpleNamespace(level="city", jurisdiction="Berkeley, CA")


@pytest.mark.parametrize(
    "clause,origin,record,kind",
    [
        # Contingencies and borrowed coverage are never cleared.
        (
            "Applies only if and when the executive office promulgates implementing regulations",
            Origin.coverage,
            CA,
            "unresolved",
        ),
        ("Residential rental property subject to the Division", Origin.coverage, CA, "unresolved"),
        ("Properties exempt under § 98.0703", Origin.exemption, CA, "unresolved"),
        # A building condition the source does not state verbatim stays open.
        ("5 or more units", Origin.coverage, CA, "unresolved"),
        # Its subject is the building's history, not an example of scope.
        (
            "All tenants in a building converted to condominium, cooperative or fee simple",
            Origin.coverage,
            NJ,
            "unresolved",
        ),
        # A transaction that mentions a covenant is not a subsidy exemption.
        (
            "Resident manager occupancy evictions are allowed only if required by law or an "
            "affordable housing covenant",
            Origin.coverage,
            LA,
            "transaction",
        ),
        # Qualifiers in an exemption list exempt nothing themselves.
        (
            "Newly constructed multiple dwellings are exempt from local rent control for 30 years",
            Origin.exemption,
            NJ,
            "qualifier",
        ),
        (
            "That exemption does not apply where there is more than one unit",
            Origin.exemption,
            CA,
            "qualifier",
        ),
        # "the Rent Ordinance" is a different law in each city.
        ("Exempt units under the Rent Ordinance", Origin.exemption, BERKELEY, "unresolved"),
        # A state rule standing aside for local rent control is precedence.
        (
            "Units subject to the City's RSO are also not covered",
            Origin.exemption,
            CA,
            "local_deference",
        ),
        ("Rental dwelling units offered by a housing provider", Origin.coverage, NJ, "scope"),
    ],
)
def test_known_traps_read_safely(clause, origin, record, kind):
    reading = classify(clause, origin, record)
    assert (reading.kind if not reading.unresolved else "unresolved") == kind


def test_an_owner_exemption_is_bounded_by_the_size_the_text_gives():
    reading = classify(
        "Dwelling units in owner-occupied premises of not more than four dwelling units",
        Origin.exemption,
        NJ,
    )
    assert reading.atoms == ((Field.owner_occupied, Op.is_true, None), (Field.units, Op.lte, 4))


def test_a_review_whose_clause_text_has_moved_no_longer_applies(tmp_path):
    source = "Rental dwelling units offered by a housing provider"
    record = make_rule(body=source, coverage_conditions=source, jurisdiction="NJ")
    compiled = compile_rule(record, source_text=source)[0]
    store = ReviewStore(tmp_path / "compiled.json")
    store.review_coverage(
        compiled,
        source_text=source,
        reviewer="Alex",
        rationale="Program scope only",
        resolved_clause_ids=["c1"],
        coverage_basis="explicit_unconditional",
        scope_evidence_span=source,
    )
    assert store.apply_reviews(compiled).review_state is ReviewState.human_approved

    review = store.reviews["r-0001"]
    review["resolved_clauses"] = [{"clause_id": "c1", "text": "Something else entirely"}]
    drifted = store.apply_reviews(compile_rule(record, source_text=source)[0])
    assert drifted.review_state is ReviewState.needs_review
    assert any("no longer matches" in n for n in drifted.notes)
