"""Regression checks at the review and submission boundaries."""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.models import Address
from app.modules.address_lookup.rule_adapter.compiler import compile_rule, content_hash
from app.modules.address_lookup.rule_adapter.models import (
    Atom,
    Field,
    InvalidCompilation,
    Op,
    Qualification,
    Relation,
    RelationType,
    ReviewState,
    SourceAnchor,
)
from app.modules.address_lookup.rule_adapter.review import ReviewStore
from app.modules.address_lookup.rule_evaluation.export import (
    SubmissionInvalid,
    to_submission,
    validate_submission,
)
from app.modules.address_lookup.rule_evaluation.predicates import (
    AddressEvidence,
    FactValue,
    evaluate_atom,
)
from app.modules.address_lookup.service import run_lookup_export
from app.modules.change_tracking.validation import sample_address_ids


def test_changed_counterpart_source_invalidates_relation_approval(tmp_path):
    left_source = "State law yields to qualifying local ordinances."
    right_source = "The local ordinance was adopted in 2018."
    candidate = Relation(
        "r-state",
        "r-city",
        "just_cause",
        RelationType.yields_to,
        SourceAnchor("yields to qualifying local ordinances"),
        condition="local ordinance adopted by the statutory cutoff",
        left_version_hash="v-left",
        right_version_hash="v-right",
        left_source_hash=content_hash(left_source),
        right_source_hash=content_hash(right_source),
    )
    store = ReviewStore(tmp_path / "rules.json")
    store.put_relations([candidate])
    assert store.review_relation(
        "r-state",
        "r-city",
        "just_cause",
        left_source_text=left_source,
        right_source_text=right_source,
        reviewer="Reviewer",
        rationale="The source dates establish the statutory qualification.",
        qualification=Qualification.confirmed,
        left_evidence_span="yields to qualifying local ordinances",
        right_evidence_span="adopted in 2018",
        valid_from=dt.date(2018, 1, 1),
    )
    assert store.all_relations()[0].review_state is ReviewState.approved

    store.put_relations([replace(candidate, right_source_hash=content_hash("Revised local law"))])
    assert store.all_relations()[0].review_state is ReviewState.needs_review


def test_legacy_phrase_approval_has_no_effect(tmp_path):
    store = ReviewStore(tmp_path / "rules.json")
    store.relations = [
        {
            "left_rule_id": "r-state",
            "right_rule_id": "r-city",
            "issue_key": "just_cause",
            "relation": "yields_to",
            "review_state": "approved",
            "source_span": "does not apply to local law",
        }
    ]
    assert store.all_relations()[0].review_state is ReviewState.needs_review


def test_san_francisco_ordinance_membership_comes_from_year_built_never_the_city():
    atom = Atom(
        "c1.1",
        Field.san_francisco_rent_ordinance_membership,
        Op.is_true,
        None,
        SourceAnchor("a rental unit covered by the Rent Ordinance"),
    )

    def built(year):
        return AddressEvidence(
            address_id="A1",
            legal_city=FactValue("San Francisco", "present"),
            legal_state=FactValue("CA", "present"),
            units=FactValue(6, "present"),
            year_built=FactValue(year, "present" if year else "not_supplied"),
            certificate_of_occupancy_date=FactValue(),
            use_code=FactValue("residential", "present"),
        )

    check = evaluate_atom(atom, built(1950))
    assert str(check.value) == "true" and check.basis == "proxy"
    # The cutoff year, a newer building (still partly covered) and a missing
    # year leave it open: being in San Francisco is never enough.
    for year in (1979, 1990, None):
        assert str(evaluate_atom(atom, built(year)).value) == "unknown"


def test_date_review_is_bound_to_rule_and_source_versions(tmp_path):
    source = "Rental law is effective 2027-07-01."
    record = SimpleNamespace(
        team_rule_id="r-date",
        effective_date="2027-07-01",
        coverage_conditions=None,
        exemptions=None,
        interaction=None,
        requirement="Rental law",
        key_value=None,
        source_doc_id="D1",
        source_url="https://example.gov/law",
        jurisdiction="CA",
        level="state",
        status="not_yet_effective",
        category="screening_restrictions",
        title="Rental law",
    )
    compiled, _ = compile_rule(record, source_text=source)
    store = ReviewStore(tmp_path / "rules.json")
    store.review_dates(
        compiled,
        source_text=source,
        reviewer="Reviewer",
        rationale="The passage states the law's effective date.",
        effective_dates=[{"raw": "2027-07-01", "source_span": source}],
    )
    assert store.date_review_for("r-date", compiled.rule_version_hash, compiled.source_hash)
    assert not store.date_review_for("r-date", compiled.rule_version_hash, "changed source")


def test_clearing_ambiguous_effective_date_needs_source_passage(tmp_path):
    source = "Annual rent increase rate effective 2025-07-01 through 2026-06-30 is 3%."
    record = SimpleNamespace(
        team_rule_id="r-rate",
        effective_date="2025-07-01",
        coverage_conditions=None,
        exemptions=None,
        interaction=None,
        requirement="Annual rent increase",
        key_value="3%",
        source_doc_id="D1",
        source_url="https://example.gov/law",
        jurisdiction="CA",
        level="state",
        status="in_force",
        category="rent_increases",
        title="Rent rate",
    )
    compiled, _ = compile_rule(record, source_text=source)
    assert compiled.effective_date_unresolved is False
    compiled.effective_date_unresolved = True
    store = ReviewStore(tmp_path / "rules.json")
    with pytest.raises(InvalidCompilation, match="source evidence"):
        store.review_dates(
            compiled,
            source_text=source,
            reviewer="Reviewer",
            rationale="The date belongs to an annual rate period.",
            effective_dates=[],
        )


class _AddressRows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _AddressRows(self.rows)


@pytest.mark.asyncio
async def test_export_rejects_an_incomplete_database_before_compilation():
    ids = sorted(sample_address_ids(settings.addresses_csv))[:490]
    session = _Session([Address(address_id=address_id) for address_id in ids])
    with pytest.raises(SubmissionInvalid, match="500 supplied sample IDs"):
        await run_lookup_export(session, dt.date(2026, 10, 1), write_audit=False)


def test_sample_id_gate_rejects_duplicates(tmp_path):
    source = tmp_path / "addresses.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["address_id"])
        writer.writeheader()
        writer.writerows({"address_id": f"A{i:04d}"} for i in range(499))
        writer.writerow({"address_id": "A0000"})
    with pytest.raises(ValueError, match="distinct"):
        sample_address_ids(source)


def test_complete_empty_export_contains_every_sample_address():
    ids = sorted(sample_address_ids(settings.addresses_csv))
    payload = to_submission([], ids, dt.date(2026, 10, 1))
    assert len(payload["lookups"]) == 500
    assert validate_submission(payload, expected_address_ids=ids, known_rule_ids=set()) == []
