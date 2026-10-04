"""Module C's gates: missing evidence, temporal transition, and export shape."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.modules.address_lookup.rule_adapter.models import (
    CompiledRule,
    CoverageBasis,
    EffectiveDate,
    Expr,
    ReviewState,
)
from app.modules.address_lookup.rule_evaluation.base import evaluate_base
from app.modules.address_lookup.rule_evaluation.predicates import AddressEvidence, FactValue
from app.modules.change_tracking import service
from app.modules.change_tracking.effective_date_review import ab325_date_evidence
from app.modules.change_tracking.schemas import CanonicalMatch, ChangeTest, ChangeTestResult
from app.modules.change_tracking.validation import (
    REQUIRED_TEST_IDS,
    build_changes_export,
    check_changes,
    sample_address_ids,
    validate_changes,
)


def _complete_payload() -> dict:
    return {
        test_id: {
            "affected_address_ids": [] if test_id == "T5" else ["A0001"],
            "notes": "Source-backed result.",
            **({"conflict_flag_address_ids": ["A0001"]} if test_id == "T3" else {}),
        }
        for test_id in REQUIRED_TEST_IDS
    }


def test_export_rejects_missing_unknown_and_duplicate_address_ids() -> None:
    payload = _complete_payload()
    payload.pop("T4")
    payload["T3"]["affected_address_ids"] = ["A9999", "A9999"]
    problems = validate_changes(payload, address_ids={"A0001"})
    assert any("T4" in problem for problem in problems)
    assert any("unknown affected" in problem for problem in problems)
    assert any("sorted and unique" in problem for problem in problems)


def test_export_requires_a_real_t3_conflict_and_empty_t5() -> None:
    payload = _complete_payload()
    payload["T3"].pop("conflict_flag_address_ids")
    payload["T5"]["affected_address_ids"] = ["A0001"]
    problems = validate_changes(payload, address_ids={"A0001"})
    assert any("T3" in problem and "conflicts" in problem for problem in problems)
    assert any("T5" in problem and "affected" in problem for problem in problems)


def test_a_complete_export_passes() -> None:
    assert validate_changes(_complete_payload(), address_ids={"A0001"}) == []


def test_sample_id_gate_uses_the_supplied_csv() -> None:
    from app.core.config import settings

    ids = sample_address_ids(settings.addresses_csv)
    assert len(ids) == 500
    assert "A0001" in ids


def test_ab325_date_requires_matching_act_and_chapter() -> None:
    source = (
        "AB 325 (AGUIAR-CURRY), CH. 338\n"
        "EFFECTIVE DATE: JANUARY 1, 2026\nCARTWRIGHT ACT: VIOLATIONS"
    )
    assert ab325_date_evidence(source) == (
        "2026-01-01",
        "AB 325 (AGUIAR-CURRY), CH. 338 EFFECTIVE DATE: JANUARY 1, 2026",
    )
    assert ab325_date_evidence(source.replace("CH. 338", "CH. 339")) is None


def test_pending_is_not_an_as_of_date_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    address = SimpleNamespace(address_id="A0001")
    rule = SimpleNamespace()
    results = iter(("pending", "applies"))
    monkeypatch.setattr(service, "_result_for", lambda *_: next(results))
    assert service._gate_opened(rule, address, dt.date(2025, 12, 31), dt.date(2026, 1, 2)) is None


@pytest.mark.asyncio
async def test_missing_canonical_rule_blocks_the_case(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(_session, canonical_id):
        return CanonicalMatch(canonical_id=canonical_id, selector="test"), []

    monkeypatch.setattr(service, "resolve_canonical", missing)
    case = ChangeTest(
        test_id="T1",
        title="date gate",
        type="as_of",
        rule_ids=["CA-ALG-01"],
        expected_behavior="transition",
        as_of_before=dt.date(2025, 12, 31),
        as_of_after=dt.date(2026, 1, 2),
    )
    result = await service.run_test(None, case)
    assert result.status == "blocked"
    assert "CA-ALG-01" in result.blocked_reason
    assert result.affected_address_ids == []
    assert not result.rules_resolved


@pytest.mark.asyncio
async def test_one_unanswerable_case_does_not_blank_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader gets the answers that exist, plus the reason for the one missing.

    `/results` feeds the UI, where four good answers withheld because a fifth
    case has no extracted ordinance is the worse failure. The blocked row has
    to be unmistakable, though: its empty affected set means "not computed",
    and anything counting buildings must be able to tell that from "nobody".
    """
    from app.modules.change_tracking.router import list_results
    from app.modules.change_tracking.schemas import ChangeTestResult

    cases = [
        ChangeTest(
            test_id="T1",
            title="date gate",
            type="as_of",
            rule_ids=["CA-ALG-01"],
            expected_behavior="transition",
            as_of_before=dt.date(2025, 12, 31),
            as_of_after=dt.date(2026, 1, 2),
        ),
        ChangeTest(
            test_id="T2",
            title="city boundary",
            type="boundary",
            rule_ids=["HOB-ALG-01"],
            expected_behavior="Hoboken only",
            as_of=dt.date(2026, 10, 1),
        ),
    ]
    answered = ChangeTestResult(
        test_id="T1",
        title="date gate",
        type="as_of",
        as_of=dt.date(2026, 1, 2),
        affected_address_ids=["A0001"],
        notes="one address moved",
        expected_behavior="transition",
    )

    real_run_test = service.run_test

    async def no_rule(_session, canonical_id):
        return CanonicalMatch(canonical_id=canonical_id, selector="test"), []

    async def run(session, test, **kwargs):
        # T2 takes the real path: a missing rule comes back blocked, not raised.
        if test.test_id == "T2":
            return await real_run_test(session, test, **kwargs)
        return answered

    class SampleIds:
        """The one query /results sends besides the replay: the sample's ids."""

        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: ["A0001"])

    monkeypatch.setattr(service, "resolve_canonical", no_rule)
    monkeypatch.setattr(service, "load_tests", lambda: cases)
    monkeypatch.setattr(service, "run_test", run)

    rows = await list_results(SampleIds())

    assert [row.test_id for row in rows] == ["T1", "T2"]
    assert rows[0].status == "complete"
    assert rows[0].blocked_reason is None
    assert rows[0].affected_address_ids == ["A0001"]

    blocked = rows[1]
    assert blocked.status == "blocked"
    assert blocked.blocked_reason is not None
    assert "HOB-ALG-01" in blocked.blocked_reason
    assert blocked.affected_address_ids == []
    assert blocked.rules_resolved is False


def test_massachusetts_bill_ids_are_source_distinct() -> None:
    selectors = service.CANONICAL_RULES
    assert selectors["MA-ALG-P1"].source_doc_ids == ("D089",)
    assert selectors["MA-ALG-P2"].source_doc_ids == ("D090",)
    assert selectors["MA-ALG-P1"].source_doc_ids != selectors["MA-ALG-P2"].source_doc_ids


@pytest.mark.asyncio
async def test_pending_case_rejects_a_live_record(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolved(_session, canonical_id):
        return (
            CanonicalMatch(canonical_id=canonical_id, selector="test", matched=True),
            [SimpleNamespace(team_rule_id=f"rule-{canonical_id}", status="in_force")],
        )

    monkeypatch.setattr(service, "resolve_canonical", resolved)
    case = ChangeTest(
        test_id="T4",
        title="pending bills",
        type="pending",
        rule_ids=["MA-ALG-P1", "MA-ALG-P2"],
        expected_behavior="if enacted",
        as_of=dt.date(2026, 10, 1),
    )
    result = await service.run_test(None, case)
    assert result.status == "blocked"
    assert "requires pending records" in result.blocked_reason


def _evidence(city: str, state: str) -> AddressEvidence:
    return AddressEvidence(
        address_id="A0001",
        legal_city=FactValue(city, "present"),
        legal_state=FactValue(state, "present"),
        units=FactValue(20, "present"),
        year_built=FactValue(2000, "present"),
        certificate_of_occupancy_date=FactValue(),
        use_code=FactValue(),
    )


def _compiled(jurisdiction: str, level: str, status: str, date: str | None = None):
    effective_dates = (
        (EffectiveDate(date, dt.date.fromisoformat(date), dt.date.fromisoformat(date), "day"),)
        if date
        else ()
    )
    return CompiledRule(
        team_rule_id=f"rule-{jurisdiction}",
        rule_version_hash="fixture",
        review_state=ReviewState.human_approved,
        coverage_basis=CoverageBasis.explicit_unconditional,
        jurisdiction=jurisdiction,
        level=level,
        status=status,
        effective_dates=effective_dates,
        coverage=Expr("all", ()),
        exemptions=Expr("any", ()),
    )


def test_real_evaluator_drives_t1_date_gate_and_state_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = _compiled("CA", "state", "in_force", "2026-01-01")
    rule = SimpleNamespace(compiled=compiled)
    monkeypatch.setattr(service.lookup_service, "evidence_for", lambda address: address.evidence)
    monkeypatch.setattr(
        service.lookup_service,
        "evaluate_rule_for_address",
        lambda record, evidence, day: SimpleNamespace(
            result=str(evaluate_base(record.compiled, evidence, day).result)
        ),
    )
    before, after = dt.date(2025, 12, 31), dt.date(2026, 1, 2)
    california = SimpleNamespace(evidence=_evidence("San Francisco", "CA"))
    new_jersey = SimpleNamespace(evidence=_evidence("Newark", "NJ"))
    assert service._gate_opened(rule, california, before, after) == (
        "not_yet_effective",
        "applies",
    )
    assert service._gate_opened(rule, new_jersey, before, after) is None


def test_real_evaluator_keeps_t2_city_boundary_and_t3_conflict_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hoboken = _compiled("Hoboken, NJ", "city", "in_force")
    jersey_city = _compiled("Jersey City, NJ", "city", "in_force")
    monkeypatch.setattr(service.lookup_service, "compiled_for", lambda rule: rule.compiled)
    monkeypatch.setattr(service.lookup_service, "evidence_for", lambda address: address.evidence)
    monkeypatch.setattr(
        service.lookup_service,
        "evaluate_rule_for_address",
        lambda record, evidence, day: SimpleNamespace(
            result=str(evaluate_base(record.compiled, evidence, day).result)
        ),
    )
    hob_rule = SimpleNamespace(compiled=hoboken)
    jc_rule = SimpleNamespace(compiled=jersey_city)
    hob_address = SimpleNamespace(evidence=_evidence("Hoboken", "NJ"))
    jc_address = SimpleNamespace(evidence=_evidence("Jersey City", "NJ"))
    newark_address = SimpleNamespace(evidence=_evidence("Newark", "NJ"))
    day = dt.date(2026, 10, 1)
    assert service._applies(hob_rule, hob_address, day)
    assert not service._applies(hob_rule, jc_address, day)
    assert service._applies(jc_rule, jc_address, day)
    assert not service._applies(jc_rule, newark_address, day)
    assert service._local_may_apply(jc_rule, jc_address, day)
    assert not service._local_may_apply(jc_rule, newark_address, day)


def test_pending_counterfactual_preserves_geography(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _compiled("MA", "state", "pending")
    monkeypatch.setattr(service.lookup_service, "compiled_for", lambda rule: rule.compiled)
    monkeypatch.setattr(service.lookup_service, "evidence_for", lambda address: address.evidence)
    rule = SimpleNamespace(compiled=pending)
    boston = SimpleNamespace(evidence=_evidence("Boston", "MA"))
    newark = SimpleNamespace(evidence=_evidence("Newark", "NJ"))
    day = dt.date(2026, 10, 1)
    assert evaluate_base(pending, boston.evidence, day).result == "pending"
    assert service._in_scope(rule, boston, day)
    assert not service._in_scope(rule, newark, day)


# ---------------------------------------------------- one case at a time ---
def _answer(test_id: str, affected: list[str], conflicts: list[str] | None = None):
    return ChangeTestResult(
        test_id=test_id,
        title=test_id,
        type="as_of",
        as_of=dt.date(2026, 10, 1),
        affected_address_ids=affected,
        conflict_flag_address_ids=conflicts or [],
        notes=f"{test_id} replayed.",
        expected_behavior="",
    )


def _cases() -> list[ChangeTest]:
    return [
        ChangeTest(test_id=t, title=t, type="as_of", rule_ids=[], expected_behavior="")
        for t in REQUIRED_TEST_IDS
    ]


@pytest.mark.asyncio
async def test_one_blocked_case_leaves_the_other_four_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = {
        "T1": _answer("T1", ["A0001"]),
        "T3": _answer("T3", ["A0001"], ["A0001"]),
        "T4": _answer("T4", ["A0002"]),
        "T5": _answer("T5", []),
    }

    async def replay(_session, test, **_kwargs):
        if test.test_id == "T2":
            return service.blocked_result(test, "no Hoboken rule")
        return answers[test.test_id]

    monkeypatch.setattr(service, "run_test", replay)
    results = await service.run_tests(None, _cases(), address_ids={"A0001", "A0002"})

    assert [r.test_id for r in results] == list(REQUIRED_TEST_IDS)
    assert {r.test_id: r.status for r in results} == {
        "T1": "complete",
        "T2": "blocked",
        "T3": "complete",
        "T4": "complete",
        "T5": "complete",
    }
    export = build_changes_export(results, address_ids={"A0001", "A0002"})
    # Left out, not exported as an empty list that would claim nothing moved.
    assert set(export.body) == {"T1", "T3", "T4", "T5"}
    assert export.omitted == {"T2": "no Hoboken rule"}
    assert not export.complete
    assert any("T2" in problem and "Hoboken" in problem for problem in export.problems)


@pytest.mark.asyncio
async def test_a_case_is_held_to_the_export_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "T1": _answer("T1", []),  # replayed fine, but moved nobody
        "T2": _answer("T2", ["A0001"]),
        "T3": _answer("T3", ["A0001"]),  # no conflict flags
        "T4": _answer("T4", ["A9999"]),  # outside the sample
        "T5": _answer("T5", []),
    }

    async def replay(_session, test, **_kwargs):
        return answers[test.test_id]

    monkeypatch.setattr(service, "run_test", replay)
    results = {r.test_id: r for r in await service.run_tests(None, _cases(), address_ids={"A0001"})}

    assert results["T1"].status == "blocked"
    assert "empty affected set" in results["T1"].blocked_reason
    assert results["T3"].status == "partial"
    assert any("conflicts are missing" in w for w in results["T3"].warnings)
    assert results["T4"].status == "blocked"
    assert results["T4"].affected_address_ids == []
    assert results["T4"].detail["withheld_affected_address_ids"] == ["A9999"]
    assert results["T5"].status == "complete"

    export = build_changes_export(results.values(), address_ids={"A0001"})
    assert set(export.body) == {"T2", "T3", "T5"}
    assert set(export.omitted) == {"T1", "T4"}


def test_a_complete_export_is_complete() -> None:
    results = [
        _answer("T1", ["A0001"]),
        _answer("T2", ["A0001"]),
        _answer("T3", ["A0001"], ["A0001"]),
        _answer("T4", ["A0001"]),
        _answer("T5", []),
    ]
    export = build_changes_export(results, address_ids={"A0001"})
    assert export.complete
    assert export.problems == []
    assert export.body["T3"]["conflict_flag_address_ids"] == ["A0001"]
    assert "conflict_flag_address_ids" not in export.body["T1"]


def test_a_sixth_case_is_incomplete_not_invalid() -> None:
    payload = _complete_payload()
    payload["T6"] = {"affected_address_ids": ["A0001"], "notes": "Hour-16 ordinance."}
    check = check_changes(payload, address_ids={"A0001"})
    assert check.invalid == {}
    assert "T6" in check.incomplete


@pytest.mark.asyncio
async def test_a_truncated_run_cannot_be_persisted() -> None:
    with pytest.raises(service.ChangeInputError):
        await service.run_tests(None, _cases(), address_limit=20, persist=True)


# ------------------------------------------------------ the date preflight ---
def _nj_t3() -> ChangeTest:
    return ChangeTest(
        test_id="T3",
        title="FAIR Act",
        type="as_of",
        rule_ids=["NJ-ALG-01"],
        expected_behavior="future, then live; flag local bans",
        as_of_before=dt.date(2026, 10, 1),
        as_of_after=dt.date(2027, 7, 2),
        states=["NJ"],
        conflict_with=["JC-ALG-01", "HOB-ALG-01"],
    )


def _wire_replay(monkeypatch: pytest.MonkeyPatch, rules: dict[str, list], addresses: list) -> None:
    """Run cases through the real service and evaluator, without a database."""

    async def resolve(_session, canonical_id):
        found = rules.get(canonical_id, [])
        return (
            CanonicalMatch(canonical_id=canonical_id, selector="test", matched=bool(found)),
            found,
        )

    async def sample(_session, _states, _limit):
        return addresses

    async def compiled_rules(_session):
        return [], {}, []

    monkeypatch.setattr(service, "resolve_canonical", resolve)
    monkeypatch.setattr(service, "_addresses_for", sample)
    monkeypatch.setattr(service.lookup_service, "compiled_for", lambda rule: rule.compiled)
    monkeypatch.setattr(service.lookup_service, "evidence_for", lambda address: address.evidence)
    monkeypatch.setattr(
        service.lookup_service,
        "evaluate_rule_for_address",
        lambda record, evidence, day: SimpleNamespace(
            result=str(evaluate_base(record.compiled, evidence, day).result)
        ),
    )
    monkeypatch.setattr(service.lookup_service, "compiled_rules", compiled_rules)
    monkeypatch.setattr(service.lookup_service, "decide_for_address", lambda *_args: [])


def _rule(rule_id: str, compiled, **fields) -> SimpleNamespace:
    return SimpleNamespace(
        team_rule_id=rule_id,
        status=compiled.status,
        effective_date=None,
        compiled=compiled,
        **fields,
    )


def _at(address_id: str, evidence: AddressEvidence) -> SimpleNamespace:
    return SimpleNamespace(address_id=address_id, evidence=evidence)


def _replay_t3(monkeypatch: pytest.MonkeyPatch, compiled, partners: dict[str, list]) -> None:
    _wire_replay(
        monkeypatch,
        {"NJ-ALG-01": [_rule("r-fair", compiled)], **partners},
        [
            _at("A0001", _evidence("Jersey City", "NJ")),
            _at("A0002", _evidence("Newark", "NJ")),
        ],
    )


@pytest.mark.asyncio
async def test_an_absent_conflict_partner_leaves_t3_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _replay_t3(monkeypatch, _compiled("NJ", "state", "not_yet_effective", "2027-07-01"), {})
    [result] = await service.run_tests(None, [_nj_t3()], address_ids={"A0001", "A0002"})
    assert result.status == "partial"
    assert result.affected_address_ids == ["A0001", "A0002"]
    assert result.conflict_flag_address_ids == []
    assert any("JC-ALG-01" in w and "HOB-ALG-01" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_a_present_conflict_partner_completes_t3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jersey_city = SimpleNamespace(
        team_rule_id="r-jc", compiled=_compiled("Jersey City, NJ", "city", "in_force")
    )
    hoboken = SimpleNamespace(
        team_rule_id="r-hob", compiled=_compiled("Hoboken, NJ", "city", "in_force")
    )
    _replay_t3(
        monkeypatch,
        _compiled("NJ", "state", "not_yet_effective", "2027-07-01"),
        {"JC-ALG-01": [jersey_city], "HOB-ALG-01": [hoboken]},
    )
    [result] = await service.run_tests(None, [_nj_t3()], address_ids={"A0001", "A0002"})
    assert result.status == "complete"
    assert result.conflict_flag_address_ids == ["A0001"]  # Jersey City, never Newark


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compiled", "why"),
    [
        (_compiled("NJ", "state", "not_yet_effective"), "has no effective date"),
        (
            replace(_compiled("NJ", "state", "in_force"), effective_date_unresolved=True),
            "needs a reviewed effective date",
        ),
        (_compiled("NJ", "state", "pending"), "recorded as pending"),
    ],
)
async def test_a_rule_without_a_usable_date_blocks_a_date_case(
    monkeypatch: pytest.MonkeyPatch, compiled, why: str
) -> None:
    _replay_t3(monkeypatch, compiled, {})
    [result] = await service.run_tests(None, [_nj_t3()], address_ids={"A0001", "A0002"})
    assert result.status == "blocked"
    assert "r-fair" in result.blocked_reason
    assert why in result.blocked_reason
    assert result.detail["undated_rules"]


# ------------------------------------------------- T2: inside its own city ---
def _awaiting_review(compiled):
    """A rule whose coverage nobody has reviewed yet: `unknown` wherever it reaches."""
    return replace(
        compiled, review_state=ReviewState.needs_review, coverage_basis=CoverageBasis.unresolved
    )


def _unplaced(state: str) -> AddressEvidence:
    """An address whose legal city the geocoder could not verify."""
    return replace(_evidence("Hoboken", state), legal_city=FactValue())


@pytest.mark.asyncio
async def test_t2_counts_unresolved_coverage_inside_its_city_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hoboken = _rule("r-hob", _awaiting_review(_compiled("Hoboken, NJ", "city", "in_force")))
    jersey_city = _rule("r-jc", _compiled("Jersey City, NJ", "city", "in_force"))
    _wire_replay(
        monkeypatch,
        {"HOB-ALG-01": [hoboken], "JC-ALG-01": [jersey_city]},
        [
            _at("A0001", _evidence("Hoboken", "NJ")),
            _at("A0002", _evidence("Jersey City", "NJ")),
            _at("A0003", _evidence("Newark", "NJ")),
            _at("A0004", _unplaced("NJ")),  # Hoboken on the envelope, unverified
        ],
    )
    case = ChangeTest(
        test_id="T2",
        title="local bans",
        type="boundary",
        rule_ids=["HOB-ALG-01", "JC-ALG-01"],
        expected_behavior="each ban only inside its own city",
        as_of=dt.date(2026, 10, 1),
    )
    [result] = await service.run_tests(
        None, [case], address_ids={"A0001", "A0002", "A0003", "A0004"}
    )

    assert result.status == "complete"
    assert result.affected_address_ids == ["A0001", "A0002"]
    assert result.detail["per_rule"] == {"HOB-ALG-01": ["A0001"], "JC-ALG-01": ["A0002"]}
    assert result.detail["per_rule_split"]["HOB-ALG-01"] == {
        "applies": [],
        "coverage_unresolved": ["A0001"],
    }
    assert result.detail["per_rule_split"]["JC-ALG-01"]["applies"] == ["A0002"]
    assert result.detail["legal_city_unresolved"] == ["A0004"]
    assert result.detail["overlap"] == []


# --------------------------------------- T5: the answer, with or without ---
def _t5() -> ChangeTest:
    return ChangeTest(
        test_id="T5",
        title="ballot question struck",
        type="negative",
        rule_ids=["MA-RENT-P1"],
        expected_behavior="no rent cap; empty set",
        as_of=dt.date(2026, 10, 1),
        states=["MA"],
    )


class _CapRules:
    """The one query T5 sends: the MA rent-cap rules its guardrail scans."""

    def __init__(self, rules: list) -> None:
        self.rules = rules

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: list(self.rules))


def _wire_t5(monkeypatch: pytest.MonkeyPatch, measure: list) -> None:
    _wire_replay(
        monkeypatch,
        {"MA-RENT-P1": measure},
        [_at("A0001", _evidence("Boston", "MA")), _at("A0002", _evidence("Cambridge", "MA"))],
    )


@pytest.mark.asyncio
async def test_t5_without_its_failed_record_is_partial_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_t5(monkeypatch, [])
    [result] = await service.run_tests(_CapRules([]), [_t5()], address_ids={"A0001", "A0002"})

    assert result.status == "partial"
    assert result.affected_address_ids == []
    assert result.detail["failed_record"] is False
    assert any("MA-RENT-P1" in w and "rent-cap check" in w for w in result.warnings)

    # Exported as the empty answer it is, but never as a complete one.
    export = build_changes_export([result], address_ids={"A0001", "A0002"})
    assert export.body["T5"]["affected_address_ids"] == []
    assert any("T5" in problem and "MA-RENT-P1" in problem for problem in export.problems)


@pytest.mark.asyncio
async def test_t5_with_its_failed_record_is_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_t5(monkeypatch, [_rule("r-ballot", _compiled("MA", "state", "failed"))])
    [result] = await service.run_tests(_CapRules([]), [_t5()], address_ids={"A0001", "A0002"})
    assert result.status == "complete"
    assert result.warnings == []
    assert result.detail["failed_record"] is True


@pytest.mark.asyncio
async def test_t5_blocks_when_a_rent_cap_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_t5(monkeypatch, [])
    boston_cap = _rule("r-cap", _compiled("Boston, MA", "city", "in_force"))
    [result] = await service.run_tests(
        _CapRules([boston_cap]), [_t5()], address_ids={"A0001", "A0002"}
    )
    assert result.status == "blocked"
    assert "guardrail failed" in result.blocked_reason
    assert result.detail["unexpected_rent_caps"] == ["A0001"]


@pytest.mark.asyncio
async def test_t5_blocks_a_measure_recorded_as_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_t5(monkeypatch, [_rule("r-ballot", _compiled("MA", "state", "in_force"))])
    [result] = await service.run_tests(_CapRules([]), [_t5()], address_ids={"A0001", "A0002"})
    assert result.status == "blocked"
    assert "requires a failed measure record" in result.blocked_reason


@pytest.mark.asyncio
async def test_t5_with_no_addresses_to_check_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_replay(monkeypatch, {"MA-RENT-P1": []}, [])
    [result] = await service.run_tests(_CapRules([]), [_t5()], address_ids={"A0001"})
    assert result.status == "blocked"
    assert "vacuous" in result.blocked_reason
