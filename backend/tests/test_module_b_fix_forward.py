"""Module B after the fix-forward: outcomes, confidence, dates, coverage.

Built on real Rule and Address rows with no database, like
test_rule_evaluation.py, so the path under test is the one the API takes.
"""

from __future__ import annotations

import datetime as dt

from app.db.models import Address, AddressJurisdiction, Document, Rule
from app.modules.address_lookup.rule_adapter.models import Op
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult
from app.modules.address_lookup.rule_evaluation.export import to_submission, validate_submission
from app.modules.address_lookup.rule_evaluation.predicates import _OPS
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
