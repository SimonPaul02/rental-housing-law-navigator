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

from app.modules.address_lookup.rule_adapter.classify import DEFERENCE
from app.modules.address_lookup.rule_adapter.models import (
    Basis,
    Qualification,
    Relation,
    RelationType,
    ReviewState,
)
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseDecision,
    BaseResult,
    CheckTrace,
    Decision,
    Reason,
    Ternary,
)


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
    """Apply current relations, leaving uncertain qualifications explicit.

    A rule another rule may govern is checked against every such governor at
    once. If one that definitely applies here is linked by a confirmed
    relation, or by the state rule's own deference to local law, this rule is
    superseded - also when its own coverage is unknown, since it is displaced
    either way. Deference nobody has reviewed is low confidence, and says so.
    Otherwise an unknown governor leaves this rule unknown, and a governor
    that does not apply leaves it as it was.
    """
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

        governors: list[tuple[Relation, BaseDecision, bool]] = []
        for rel in relations:
            if rel.left_rule_id != base.team_rule_id or rel.issue_key != base.issue_key:
                continue
            other = by_id.get(rel.right_rule_id)
            if other is None or other.issue_key != base.issue_key:
                continue

            confirmed = (
                rel.review_state is ReviewState.approved
                and rel.qualification is Qualification.confirmed
                and (rel.valid_from is None or base.as_of >= rel.valid_from)
            )
            if rel.relation is RelationType.both_apply:
                if confirmed:
                    final.interaction_checks.append(
                        CheckTrace(
                            check="interaction",
                            value=Ternary.true,
                            reason=Reason.both_apply,
                            detail=(
                                f"{rel.right_rule_id} governs the same issue and the sources "
                                "say both apply, so neither displaces the other."
                            ),
                            source_span=rel.anchor.source_span if rel.anchor else None,
                        )
                    )
                continue

            if rel.relation is RelationType.possible_conflict:
                if confirmed:
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
            if (
                rel.review_state is ReviewState.approved
                and rel.qualification is Qualification.excluded
            ):
                continue
            if (
                rel.review_state is ReviewState.approved
                and rel.qualification is Qualification.confirmed
                and rel.valid_from
                and base.as_of < rel.valid_from
            ):
                continue  # confirmed, but the precedence starts later
            governors.append((rel, other, confirmed))

        displacing = [
            (rel, other, confirmed)
            for rel, other, confirmed in governors
            if other.result is BaseResult.applies and (confirmed or rel.basis == DEFERENCE)
        ]
        if displacing:
            # A confirmed link outranks an inferred one as the reported reason.
            rel, other, confirmed = sorted(displacing, key=lambda g: not g[2])[0]
            final.result = BaseResult.superseded
            final.superseded_by = other.team_rule_id
            hold = f" A reviewer noted: {rel.review_note}" if rel.review_note else ""
            final.interaction_checks.append(
                CheckTrace(
                    check="interaction",
                    value=Ternary.true,
                    reason=Reason.superseded_by,
                    detail=(
                        f"{other.team_rule_id} also applies to this address and governs "
                        f"{rel.issue_key}"
                        + (
                            " under a reviewed relation, so this rule is superseded here."
                            if confirmed
                            else ", and this rule's own text defers to local law there, so it "
                            "is superseded here." + hold
                        )
                    ),
                    source_span=rel.anchor.source_span if rel.anchor else None,
                    basis=None if confirmed else str(Basis.inferred),
                )
            )
            out.append(final)
            continue

        for rel, other, confirmed in governors:
            if other.result is BaseResult.applies and not confirmed:
                # Unreviewed, and not deference: whether it displaces this rule
                # is a question for a reviewer, not something to infer.
                final.result = BaseResult.unknown
                final.conflict_flag = True
                final.conflict_reason = (
                    f"Whether {rel.right_rule_id} displaces this rule is unverified: "
                    f"{rel.condition or 'the relationship needs source review'}."
                )
                final.interaction_checks.append(
                    CheckTrace(
                        check="interaction",
                        value=Ternary.unknown,
                        reason=Reason.relation_qualification_unknown,
                        detail=final.conflict_reason,
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
                break
            # other.result does not apply -> this rule keeps its base result.

        out.append(final)

    return out
