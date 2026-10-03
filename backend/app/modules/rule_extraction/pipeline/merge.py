"""Merge candidate rules from many documents into one record per law.

One rule = jurisdiction x category x law. Several documents may describe the
same law (the ordinance, a city FAQ, a Justia mirror, a law-firm note); they
collapse into a single record whose fields come from the most trustworthy
source that actually states each one.

`team_rule_id` is a hash of the group key rather than a counter. That matters
because lookups.json and changes.json reference these ids: with sequential ids
a re-extraction that finds one extra rule renumbers everything after it and
silently invalidates both downstream files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.modules.rule_extraction.pipeline.inventory import TIER_RANK, tier_trust

# Fields taken from the best source that states them.
MERGEABLE = (
    "title",
    "requirement",
    "key_value",
    "coverage_conditions",
    "exemptions",
    "interaction",
    "effective_date",
)
# Disagreement on these is a substantive conflict a human should see.
CONFLICT_FIELDS = ("effective_date", "key_value", "status")

# Most conservative first: if sources disagree, we report the weaker claim.
_STATUS_CAUTION = ["failed", "pending", "not_yet_effective", "in_force"]


@dataclass(slots=True)
class Candidate:
    """One extracted rule, before merging, with its source's provenance."""

    doc_id: str
    tier: str
    origin: str  # provided | fetched | organizer_release
    retrieved_at: str | None
    source_url: str
    citation: str  # canonical
    citation_evidence: str  # none | text | section | url
    fields: dict[str, Any]  # jurisdiction, level, category, status, + MERGEABLE
    quoted_span: str
    model_confidence: float | None = None


@dataclass(slots=True)
class MergedRule:
    team_rule_id: str
    group_key: str
    main_doc_id: str
    tier: str
    fields: dict[str, Any]
    quoted_span: str
    source_url: str
    citation: str
    citation_evidence: str
    confidence: float
    conflict_flag: bool
    conflict_note: str | None
    other_sources: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


def group_key_for(jurisdiction: str, category: str, citation: str) -> str:
    """Identity of a law. Normalised so trivial formatting differences between
    sources do not split one law into two rules."""
    return "|".join(part.strip().casefold() for part in (jurisdiction, category, citation))


def rule_id_for(group_key: str) -> str:
    digest = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:8]
    return f"r-{digest}"


def _rank(c: Candidate) -> tuple:
    """Lower sorts better: tier, then provided over fetched, then newest."""
    origin_rank = {"provided": 0, "organizer_release": 0, "fetched": 1}.get(c.origin, 2)
    return (
        TIER_RANK.get(c.tier, 9),
        origin_rank,
        # Newest retrieval wins, so invert by sorting on the negated string key.
        _negated(c.retrieved_at or ""),
        c.doc_id,
    )


def _negated(value: str) -> tuple:
    # Sort descending on a string without reversing the whole tuple.
    return tuple(-ord(ch) for ch in value)


def merge_group(candidates: list[Candidate]) -> MergedRule:
    """Collapse all candidates describing one law into a single rule."""
    ordered = sorted(candidates, key=_rank)
    main = ordered[0]
    others = ordered[1:]

    merged: dict[str, Any] = dict(main.fields)
    field_source = {k: main.doc_id for k in merged if merged.get(k) is not None}

    # Fill gaps from the next-best source that actually states the field.
    for cand in others:
        for key in MERGEABLE:
            if merged.get(key) in (None, "") and cand.fields.get(key) not in (None, ""):
                merged[key] = cand.fields[key]
                field_source[key] = cand.doc_id

    conflicts: list[dict] = []
    for key in CONFLICT_FIELDS:
        main_value = merged.get(key)
        disagreeing = {
            c.doc_id: c.fields.get(key)
            for c in others
            if c.fields.get(key) not in (None, "") and _differs(c.fields.get(key), main_value)
        }
        if disagreeing:
            conflicts.append(
                {
                    "field": key,
                    "main_value": main_value,
                    "main_doc_id": field_source.get(key, main.doc_id),
                    "other_values": disagreeing,
                }
            )

    # Status is special: prefer the more cautious claim over the higher tier,
    # because reporting a pending bill as in force is the worse error.
    status_conflict = next((c for c in conflicts if c["field"] == "status"), None)
    if status_conflict:
        all_status = [merged.get("status"), *status_conflict["other_values"].values()]
        merged["status"] = min(
            (s for s in all_status if s),
            key=lambda s: _STATUS_CAUTION.index(s) if s in _STATUS_CAUTION else 99,
        )

    agreeing = sum(
        1
        for c in others
        if not any(
            _differs(c.fields.get(f), merged.get(f))
            for f in CONFLICT_FIELDS
            if c.fields.get(f) not in (None, "")
        )
    )
    confidence = _confidence(main, agreeing, len(conflicts), candidates)

    group_key = group_key_for(
        merged.get("jurisdiction", ""), merged.get("category", ""), main.citation
    )

    note = None
    if conflicts:
        note = "; ".join(
            f"{c['field']}: main={c['main_value']!r} from {c['main_doc_id']}, "
            f"also {c['other_values']}"
            for c in conflicts
        )[:1000]

    return MergedRule(
        team_rule_id=rule_id_for(group_key),
        group_key=group_key,
        main_doc_id=main.doc_id,
        tier=main.tier,
        fields=merged,
        quoted_span=main.quoted_span,
        source_url=main.source_url,
        citation=main.citation,
        citation_evidence=main.citation_evidence,
        confidence=confidence,
        conflict_flag=bool(conflicts),
        conflict_note=note,
        other_sources=[
            {
                "doc_id": c.doc_id,
                "tier": c.tier,
                "origin": c.origin,
                "url": c.source_url,
            }
            for c in others
        ],
        conflicts=conflicts,
    )


def _differs(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() != b.strip().casefold()
    return a != b


def _confidence(
    main: Candidate, agreeing: int, conflict_count: int, all_candidates: list[Candidate]
) -> float:
    score = tier_trust(main.tier)
    score += 0.05 * agreeing
    score -= 0.15 * conflict_count
    if main.citation_evidence == "none":
        score -= 0.20
    # Only ever as good as the best source backing it.
    if all(c.tier == "4" for c in all_candidates):
        score = min(score, 0.60)
    return round(max(0.0, min(1.0, score)), 2)


def merge_all(candidates: list[Candidate]) -> list[MergedRule]:
    groups: dict[str, list[Candidate]] = {}
    for cand in candidates:
        key = group_key_for(
            cand.fields.get("jurisdiction", ""),
            cand.fields.get("category", ""),
            cand.citation,
        )
        groups.setdefault(key, []).append(cand)
    return [merge_group(group) for group in groups.values()]
