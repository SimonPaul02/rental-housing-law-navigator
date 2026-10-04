"""Confidence: how much of an answer rests on the machine's own reading.

An answer is `high` when every check behind it is the cited source text or a
named reviewer's decision, and `low` as soon as one check rests on anything
weaker - a clause the classifier read, a fact presumed from the parcel record,
a precedence link inferred from deference language, a date the source does
not state. The reasons name which, so a low answer can be reviewed rather
than distrusted wholesale.

Computed after the interaction pass, because a superseded rule is only as
certain as the rule that displaced it.
"""

from __future__ import annotations

from app.modules.address_lookup.rule_adapter.models import Basis, CompiledRule, ReviewState
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseResult,
    CheckTrace,
    Confidence,
    Decision,
)

_WHY = {
    Basis.machine_read: "a clause was read by the machine and not yet reviewed",
    Basis.presumed: "a fact is presumed from the parcel record",
    Basis.proxy: "one fact stands in for another",
    Basis.inferred: "the precedence between the two rules is inferred, not reviewed",
    Basis.unverified_date: "the effective date comes from the rule record, not the source",
}


def _reason(trace: CheckTrace) -> str:
    basis = Basis(trace.basis)
    detail = (trace.detail or "").strip().rstrip(".")
    if len(detail) > 160:
        detail = detail[:157].rstrip() + "..."
    return f"{_WHY[basis]} ({detail})" if detail else _WHY[basis]


def assess(decision: Decision, rule: CompiledRule) -> None:
    """Set `confidence` and its reasons on one decision, from its own checks."""
    reasons: list[str] = []
    for trace in (*decision.base.checks, *decision.interaction_checks):
        if trace.basis and Basis(trace.basis).is_low:
            reason = _reason(trace)
            if reason not in reasons:
                reasons.append(reason)
    if not reasons and rule.review_state is ReviewState.machine_classified:
        reasons.append("the rule's clauses were classified by the machine, not reviewed")
    decision.confidence = Confidence.low if reasons else Confidence.high
    decision.confidence_reasons = reasons


def assess_all(decisions: list[Decision], compiled: dict[str, CompiledRule]) -> None:
    """Assess every decision for one address, then let displacement inherit."""
    for decision in decisions:
        assess(decision, compiled[decision.team_rule_id])
    by_id = {d.team_rule_id: d for d in decisions}
    for decision in decisions:
        governor = by_id.get(decision.superseded_by or "")
        if (
            decision.result is BaseResult.superseded
            and governor is not None
            and governor.confidence is Confidence.low
        ):
            reason = (
                f"the rule that displaces it, {governor.team_rule_id}, is itself low confidence"
            )
            decision.confidence = Confidence.low
            if reason not in decision.confidence_reasons:
                decision.confidence_reasons.append(reason)
