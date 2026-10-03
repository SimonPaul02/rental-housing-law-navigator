"""Merge stage: source precedence, conflicts, stable ids, confidence."""

from __future__ import annotations

from app.modules.rule_extraction.pipeline.merge import (
    Candidate,
    group_key_for,
    merge_all,
    merge_group,
    rule_id_for,
)


def cand(doc_id, tier, *, origin="provided", status="in_force", **fields):
    base = {
        "jurisdiction": "Berkeley, CA",
        "level": "city",
        "category": "algorithmic_rent_setting",
        "status": status,
        "title": "Ban",
        "requirement": "No coordinated pricing algorithms.",
    }
    base.update(fields)
    return Candidate(
        doc_id=doc_id,
        tier=tier,
        origin=origin,
        retrieved_at="2026-10-01T00:00Z",
        source_url=f"https://example.gov/{doc_id}",
        citation="Berkeley Mun. Code ch. 13.63",
        citation_evidence="text",
        fields=base,
        quoted_span="x" * 30,
    )


# -- identity ---------------------------------------------------------------
def test_rule_id_is_stable_across_runs():
    k = group_key_for("NJ", "security_deposits", "N.J.S.A. 46:8-21.2")
    assert rule_id_for(k) == rule_id_for(k)


def test_rule_id_ignores_case_and_whitespace():
    a = group_key_for("Berkeley, CA", "security_deposits", "BMC 13.63")
    b = group_key_for(" berkeley, ca ", "SECURITY_DEPOSITS", "BMC 13.63 ")
    assert rule_id_for(a) == rule_id_for(b)


def test_adding_a_rule_does_not_renumber_the_others():
    """The whole point of hashing: sequential ids would shift here and break
    every team_rule_id reference in lookups.json."""
    first = merge_all([cand("D001", "1")])
    later = merge_all([cand("D001", "1"), cand("D099", "1", category="security_deposits")])
    original = {r.team_rule_id for r in first}
    assert original.issubset({r.team_rule_id for r in later})


# -- source precedence ------------------------------------------------------
def test_best_tier_becomes_main_source():
    merged = merge_group([cand("D037", "4"), cand("D001", "1"), cand("D019", "3")])
    assert merged.main_doc_id == "D001"
    assert merged.tier == "1"
    assert [o["doc_id"] for o in merged.other_sources] == ["D019", "D037"]


def test_provided_beats_fetched_at_equal_tier():
    merged = merge_group(
        [cand("D050", "2", origin="fetched"), cand("D040", "2", origin="provided")]
    )
    assert merged.main_doc_id == "D040"


def test_gap_is_filled_from_a_weaker_source():
    """A secondary source may legitimately supply a field the ordinance omits."""
    merged = merge_group(
        [cand("D001", "1", key_value=None), cand("D037", "4", key_value="$1,000 per violation")]
    )
    assert merged.fields["key_value"] == "$1,000 per violation"
    assert merged.main_doc_id == "D001"


# -- conflicts --------------------------------------------------------------
def test_disagreeing_effective_dates_raise_a_conflict():
    merged = merge_group(
        [
            cand("D001", "1", effective_date="2026-03-01"),
            cand("D002", "4", effective_date="2026-01-01"),
        ]
    )
    assert merged.conflict_flag
    assert merged.conflicts[0]["field"] == "effective_date"
    assert merged.fields["effective_date"] == "2026-03-01"  # main source wins
    assert "D002" in merged.conflict_note


def test_agreement_raises_no_conflict():
    merged = merge_group(
        [
            cand("D001", "1", effective_date="2026-03-01"),
            cand("D002", "4", effective_date="2026-03-01"),
        ]
    )
    assert not merged.conflict_flag
    assert merged.conflicts == []


def test_status_conflict_takes_the_more_cautious_claim():
    """Reporting a pending bill as in force is the worse error, so caution
    beats tier here even though the law_text source ranks higher."""
    merged = merge_group(
        [cand("D001", "1", status="in_force"), cand("D045", "1b", status="pending")]
    )
    assert merged.fields["status"] == "pending"
    assert merged.conflict_flag


def test_missing_field_is_not_a_conflict():
    merged = merge_group(
        [cand("D001", "1", effective_date="2026-03-01"), cand("D002", "4", effective_date=None)]
    )
    assert not merged.conflict_flag


# -- confidence -------------------------------------------------------------
def test_agreeing_sources_raise_confidence():
    """Measured on a tier-2 main source: tier 1 is already at 1.00, so the
    agreement bonus is invisible there (see test below)."""
    alone = merge_group([cand("D040", "2")]).confidence
    backed = merge_group([cand("D040", "2"), cand("D019", "3")]).confidence
    assert backed > alone


def test_tier_one_is_already_at_the_ceiling():
    """Worth pinning: under the spec's weights a tier-1 rule scores exactly
    1.00 whether or not anything corroborates it, so `confidence` carries no
    signal for the largest group of rules. Lower tier-1 trust to ~0.95 if the
    dev key rewards a confidence spread."""
    assert merge_group([cand("D001", "1")]).confidence == 1.0
    assert merge_group([cand("D001", "1"), cand("D019", "3")]).confidence == 1.0


def test_conflicts_lower_confidence():
    clean = merge_group([cand("D001", "1")]).confidence
    conflicted = merge_group(
        [
            cand("D001", "1", effective_date="2026-03-01"),
            cand("D002", "1", effective_date="2026-01-01"),
        ]
    ).confidence
    assert conflicted < clean


def test_secondary_only_is_capped():
    merged = merge_group([cand("D035", "4"), cand("D037", "4")])
    assert merged.confidence <= 0.60


def test_unverified_citation_is_penalised():
    c = cand("D001", "1")
    c.citation_evidence = "none"
    assert merge_group([c]).confidence < merge_group([cand("D001", "1")]).confidence


# -- grouping ---------------------------------------------------------------
def test_same_law_different_documents_is_one_rule():
    rules = merge_all([cand("D001", "1"), cand("D019", "3"), cand("D037", "4")])
    assert len(rules) == 1


def test_different_categories_stay_separate():
    rules = merge_all([cand("D001", "1"), cand("D001b", "1", category="security_deposits")])
    assert len(rules) == 2
