"""The interaction pass: which rule governs an issue when several cover it.

Run after base evaluation, per address and issue. The rule it enforces is that
supersession has to be *earned*:

  * a reviewed, issue-specific relation must say the other rule governs;
  * that other rule must itself definitely apply to this address.

If the governing rule's own answer is unknown, the dependent rule's answer
becomes unknown too and says why. If the governing rule does not apply, the
dependent rule keeps its base result. There is no fallback where a city rule
beats a state rule because it is local - that default is a guess, and a state
and a city rule can perfectly well both apply.
"""

from __future__ import annotations

from app.modules.address_lookup.rule_adapter.models import Relation, RelationType, ReviewState
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseDecision,
    BaseResult,
    CheckTrace,
    Decision,
    Reason,
    Ternary,
)


def _approved(relations: list[Relation]) -> list[Relation]:
    return [r for r in relations if r.review_state is ReviewState.approved]


def find_cycles(relations: list[Relation]) -> list[tuple[str, ...]]:
    """Directed cycles among `yields_to` relations, per issue.

    A cycle means two rules each claim to give way to the other, which cannot
    be resolved without reading the law again - so it goes to review rather
    than being broken by rule id or source order.
    """
    edges: dict[tuple[str, str], list[str]] = {}
    for rel in relations:
        if rel.relation is RelationType.yields_to:
            edges.setdefault((rel.issue_key, rel.left_rule_id), []).append(rel.right_rule_id)

    cycles: list[tuple[str, ...]] = []
    for issue, start in list(edges):
        seen: set[str] = set()
        stack = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for nxt in edges.get((issue, node), []):
                if nxt == start:
                    cycles.append(path + (nxt,))
                elif nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + (nxt,)))
    return cycles


def resolve_interactions(
    decisions: list[BaseDecision], relations: list[Relation]
) -> list[Decision]:
    """Apply reviewed relations to one address' base decisions."""
    approved = _approved(relations)
    by_id = {d.team_rule_id: d for d in decisions}
    out: list[Decision] = []

    for base in decisions:
        final = Decision(
            base=base,
            result=base.result,
            conflict_flag=base.conflict_flag,
            conflict_reason=base.conflict_reason,
        )

        # Only a rule that is otherwise live can be superseded. A rule that
        # does not apply, is pending or is not yet effective has nothing to
        # yield.
        if base.result not in (BaseResult.applies, BaseResult.unknown):
            out.append(final)
            continue

        for rel in approved:
            if rel.left_rule_id != base.team_rule_id or rel.issue_key != base.issue_key:
                continue
            other = by_id.get(rel.right_rule_id)
            if other is None or other.issue_key != base.issue_key:
                continue

            if rel.relation is RelationType.both_apply:
                final.interaction_checks.append(
                    CheckTrace(
                        check="interaction",
                        value=Ternary.true,
                        reason=Reason.both_apply,
                        detail=(
                            f"{rel.right_rule_id} governs the same issue and the sources say "
                            "both apply, so neither displaces the other."
                        ),
                        source_span=rel.anchor.source_span if rel.anchor else None,
                    )
                )
                continue

            if rel.relation is RelationType.possible_conflict:
                final.conflict_flag = True
                final.conflict_reason = (
                    f"Possible conflict with {rel.right_rule_id} on {rel.issue_key}: "
                    f"{rel.condition or 'the sources do not settle which governs'}."
                )
                final.interaction_checks.append(
                    CheckTrace(
                        check="interaction",
                        value=Ternary.unknown,
                        reason=Reason.possible_conflict,
                        detail=final.conflict_reason,
                        source_span=rel.anchor.source_span if rel.anchor else None,
                    )
                )
                continue

            # yields_to / bars_local: the other rule may govern this issue.
            if other.result is BaseResult.applies:
                final.result = BaseResult.superseded
                final.superseded_by = other.team_rule_id
                final.interaction_checks.append(
                    CheckTrace(
                        check="interaction",
                        value=Ternary.true,
                        reason=Reason.superseded_by,
                        detail=(
                            f"{other.team_rule_id} also applies to this address and governs "
                            f"{rel.issue_key} under a reviewed relation, so this rule is "
                            "superseded here."
                        ),
                        source_span=rel.anchor.source_span if rel.anchor else None,
                    )
                )
                break
            if other.result is BaseResult.unknown:
                final.result = BaseResult.unknown
                final.interaction_checks.append(
                    CheckTrace(
                        check="interaction",
                        value=Ternary.unknown,
                        reason=Reason.governing_rule_unknown,
                        detail=(
                            f"Whether {other.team_rule_id} governs {rel.issue_key} here is "
                            "itself unknown, so whether this rule is displaced is unknown too."
                        ),
                        source_span=rel.anchor.source_span if rel.anchor else None,
                    )
                )
            # other.result does not apply -> this rule keeps its base result.

        out.append(final)

    return out
