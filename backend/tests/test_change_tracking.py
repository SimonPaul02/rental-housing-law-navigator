"""Module C's gates: missing evidence, temporal transition, and export shape."""

from __future__ import annotations

import datetime as dt
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
from app.modules.change_tracking.schemas import CanonicalMatch, ChangeTest
from app.modules.change_tracking.validation import (
    REQUIRED_TEST_IDS,
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
async def test_missing_canonical_rule_stops_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pytest.raises(service.ChangeInputError, match="CA-ALG-01"):
        await service.run_test(None, case, persist=False)


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
    with pytest.raises(service.ChangeInputError, match="requires pending records"):
        await service.run_test(None, case, persist=False)


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
