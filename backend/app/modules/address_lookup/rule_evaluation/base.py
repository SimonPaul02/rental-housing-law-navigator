"""The base decision: geography, then time, then coverage and exemptions.

Order matters for the explanation as much as for the result. A rule from
another state should say so and stop; a rule that is still a bill should say
that rather than reporting on a unit count; and a building plainly outside a
rule's coverage is outside it whether or not an owner name is missing.
"""

from __future__ import annotations

import datetime as dt

from app.modules.address_lookup.rule_adapter.models import CompiledRule, ReviewState
from app.modules.address_lookup.rule_evaluation import predicates as pred
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseDecision,
    BaseResult,
    CheckTrace,
    Reason,
    Ternary,
)
from app.modules.address_lookup.rule_evaluation.timing import evaluate_time


def evaluate_geography(
    rule: CompiledRule, evidence: pred.AddressEvidence
) -> tuple[Ternary, CheckTrace]:
    """State rules match the state; city rules match the *verified legal* city.

    The mailing city is not the legal city - 37 of the sample addresses are
    Boston neighbourhoods and one is San Diego's San Ysidro - so an unresolved
    legal city gives unknown for a city rule that could plausibly reach it,
    never a guess from the postal line.
    """
    state_fact = evidence.legal_state
    address_state = (str(state_fact.value) if state_fact.usable else "").strip().upper()

    if rule.level == "state":
        want = rule.jurisdiction.strip().upper()
        held = want == address_state
        return (
            Ternary.true if held else Ternary.false,
            CheckTrace(
                check="geography",
                value=Ternary.true if held else Ternary.false,
                reason=Reason.jurisdiction_match if held else Reason.jurisdiction_mismatch,
                detail=(
                    f"State rule for {want}; this address is in "
                    f"{address_state or 'an unknown state'}."
                ),
                field="legal_state",
                fact_value=address_state or None,
            ),
        )

    want_city, _, want_state = rule.jurisdiction.rpartition(",")
    want_city = (want_city or rule.jurisdiction).strip()
    want_state = want_state.strip().upper() or address_state

    if want_state and address_state and want_state != address_state:
        return (
            Ternary.false,
            CheckTrace(
                check="geography",
                value=Ternary.false,
                reason=Reason.jurisdiction_mismatch,
                detail=(
                    f"City rule for {want_city}, {want_state}; this address is in "
                    f"{address_state}, a different state."
                ),
                field="legal_state",
                fact_value=address_state,
            ),
        )

    city_fact = evidence.legal_city
    if not city_fact.usable:
        return (
            Ternary.unknown,
            CheckTrace(
                check="geography",
                value=Ternary.unknown,
                reason=Reason.legal_city_unresolved,
                detail=(
                    f"City rule for {want_city}. The legal city for this address is not "
                    f"verified ({evidence.jurisdiction_note or 'no verified resolution'}), and "
                    "the mailing city is not the legal city, so whether the rule reaches it "
                    "is unknown."
                ),
                field="legal_city",
                fact_status=city_fact.status,
            ),
        )

    got = str(city_fact.value).strip()
    held = got.casefold() == want_city.casefold()
    return (
        Ternary.true if held else Ternary.false,
        CheckTrace(
            check="geography",
            value=Ternary.true if held else Ternary.false,
            reason=Reason.jurisdiction_match if held else Reason.jurisdiction_mismatch,
            detail=(
                f"City rule for {want_city}, {want_state}; this address resolves to "
                f"{got}, {address_state}"
                + (f" by {evidence.jurisdiction_method}." if evidence.jurisdiction_method else ".")
            ),
            field="legal_city",
            fact_value=got,
        ),
    )


def evaluate_base(
    rule: CompiledRule, evidence: pred.AddressEvidence, as_of: dt.date
) -> BaseDecision:
    """One rule, one address, one date - before interactions."""
    decision = BaseDecision(
        team_rule_id=rule.team_rule_id,
        address_id=evidence.address_id,
        as_of=as_of,
        result=BaseResult.unknown,
        issue_key=rule.issue_key,
    )

    # 1. Geography. A definite mismatch is the end of it.
    geo, geo_trace = evaluate_geography(rule, evidence)
    decision.geography = geo
    decision.checks.append(geo_trace)
    if geo is Ternary.false:
        decision.result = BaseResult.does_not_apply
        return decision

    # 2. Time. A failed rule drops out entirely; pending and future rules are
    #    carried forward, because the challenge asks which addresses *would* be
    #    affected - but they are still filtered by coverage below.
    in_force, timed_result, time_trace = evaluate_time(rule, as_of)
    decision.time = in_force
    decision.checks.append(time_trace)
    if timed_result is BaseResult.failed:
        decision.result = BaseResult.failed
        return decision

    # 3. Coverage and exemptions pull in opposite directions and are evaluated
    #    separately: coverage must hold, an exemption must not.
    coverage, coverage_traces = pred.evaluate_expr(rule.coverage, evidence)
    exemption, exemption_traces = pred.evaluate_expr(rule.exemptions, evidence)

    if rule.coverage.is_empty:
        coverage = Ternary.true if rule.review_state is ReviewState.approved else Ternary.unknown
        coverage_traces = [
            CheckTrace(
                check="coverage",
                value=coverage,
                reason=(
                    Reason.unconditional_reviewed
                    if coverage is Ternary.true
                    else Reason.rule_not_approved
                ),
                detail=(
                    "The rule states no building-level condition this data could narrow; "
                    "a reviewer confirmed it covers its jurisdiction unconditionally."
                    if coverage is Ternary.true
                    else "The rule has no reviewed coverage condition, so coverage is unknown."
                ),
            )
        ]

    decision.checks.extend(coverage_traces)
    decision.checks.extend(exemption_traces)

    # 4. Anything the compiler could not translate makes a non-false answer
    #    unknown. A definite false still stands: an untranslated clause cannot
    #    bring a building back inside a rule that plainly excludes it.
    if rule.has_unmapped:
        decision.checks.append(
            CheckTrace(
                check="coverage",
                value=Ternary.unknown,
                reason=Reason.rule_clause_unmapped,
                detail=(
                    f"{len(rule.unmapped_text)} clause(s) of this rule could not be "
                    "translated into a checkable condition and are awaiting review: "
                    + "; ".join(u.text[:120] for u in rule.unmapped_text[:2])
                ),
            )
        )

    unmapped_blocks = rule.has_unmapped

    decision.coverage = coverage
    decision.exemption = exemption
    decision.unresolved_fields = pred.unresolved_fields([*coverage_traces, *exemption_traces])

    # 5. Fold it together. Definite exclusions first, so an unresolvable fact
    #    never upgrades a building that is plainly outside the rule.
    if coverage is Ternary.false:
        decision.result = BaseResult.does_not_apply
        return decision
    if exemption is Ternary.true:
        decision.result = BaseResult.does_not_apply
        decision.checks.append(
            CheckTrace(
                check="exemption",
                value=Ternary.true,
                reason=Reason.fact_satisfied,
                detail=(
                    "The building falls inside the rule's exemption, so the rule does not apply."
                ),
            )
        )
        return decision

    # Geography that could not be resolved is just as disqualifying as coverage
    # that could not be: a city rule whose reach is unknown cannot be reported
    # as applying.
    uncertain = (
        geo is Ternary.unknown
        or coverage is Ternary.unknown
        or exemption is Ternary.unknown
        or unmapped_blocks
    )
    if geo is Ternary.unknown and "legal_city" not in decision.unresolved_fields:  # noqa: E501
        decision.unresolved_fields.insert(0, "legal_city")

    # 6. A pending or future rule keeps its temporal answer, now that coverage
    #    has had its chance to exclude the address outright.
    if timed_result in (BaseResult.pending, BaseResult.not_yet_effective):
        decision.result = timed_result
        if uncertain:
            decision.checks.append(
                CheckTrace(
                    check="coverage",
                    value=Ternary.unknown,
                    reason=Reason.fact_not_supplied,
                    detail=(
                        "Whether this address would be covered if the rule took effect is "
                        "not settled by the supplied data."
                    ),
                )
            )
        return decision
    if timed_result is BaseResult.unknown or in_force is Ternary.unknown:
        decision.result = BaseResult.unknown
        if time_trace.reason is Reason.conflicting_effective_dates:
            decision.conflict_flag = True
            decision.conflict_reason = time_trace.detail
        return decision

    if uncertain:
        decision.result = BaseResult.unknown
        return decision

    decision.result = BaseResult.applies
    if not rule.exemptions.is_empty and exemption is Ternary.false:
        decision.checks.append(
            CheckTrace(
                check="exemption",
                value=Ternary.false,
                reason=Reason.fact_violated,
                detail="The rule's exemption cannot apply to this building, so the rule stands.",
            )
        )
    return decision
