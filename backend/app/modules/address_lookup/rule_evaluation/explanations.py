"""Explanations built from the checks that actually ran.

Not model-written prose. Every sentence here is a rendering of a `CheckTrace`,
so an explanation cannot claim a reason the evaluator did not use, and the
decisive fact is always named. That is the difference between "coverage is
unknown" and "the certificate-of-occupancy date is not supplied and the San
Francisco cutoff is 1979-06-13, so which side of it this building falls on
cannot be determined".
"""

from __future__ import annotations

from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseResult,
    CheckTrace,
    Confidence,
    Decision,
    Reason,
    Ternary,
)

#: Reasons that explain an unknown and should lead the sentence.
_DECISIVE_UNKNOWN = (
    Reason.certificate_cutoff_year_ambiguous,
    Reason.certificate_date_not_supplied,
    Reason.conflicting_effective_dates,
    Reason.inside_effective_interval,
    Reason.effective_date_missing,
    Reason.effective_date_unverified,
    Reason.legal_city_unresolved,
    Reason.rule_clause_unmapped,
    Reason.governing_rule_unknown,
    Reason.relation_qualification_unknown,
    Reason.fact_never_supplied,
    Reason.fact_conflicted,
    Reason.fact_invalid,
    Reason.fact_wrong_scope,
    Reason.fact_not_supplied,
)


def _pick(checks: list[CheckTrace], value: Ternary, reasons=()) -> list[CheckTrace]:
    chosen = [c for c in checks if c.value is value]
    if reasons:
        ordered = []
        for reason in reasons:
            ordered.extend(c for c in chosen if c.reason is reason)
        ordered.extend(c for c in chosen if c not in ordered)
        return ordered
    return chosen


def explain(decision: Decision) -> str:
    """One to three sentences naming the decisive checks."""
    base = decision.base
    checks = [*base.checks, *decision.interaction_checks]
    parts: list[str] = []

    if decision.result is BaseResult.superseded:
        parts += [c.detail for c in _pick(checks, Ternary.true, (Reason.superseded_by,))][:1]
        geo = _pick(base.checks, Ternary.true, (Reason.jurisdiction_match,))
        if geo:
            parts.insert(0, geo[0].detail)

    elif decision.result is BaseResult.does_not_apply:
        if base.exemption is Ternary.true:
            # The exemption that held is the reason, not the ones that failed.
            held = [
                c
                for c in base.checks
                if c.value is Ternary.true and (c.condition_id or "").startswith("x")
            ]
            parts += [c.detail for c in held][:1] + [
                c.detail for c in base.checks if c.check == "exemption"
            ][:1]
        else:
            parts += [c.detail for c in _pick(checks, Ternary.false)][:2]

    elif decision.result is BaseResult.failed:
        parts += [c.detail for c in checks if c.reason is Reason.status_failed][:1]

    elif decision.result is BaseResult.pending:
        parts += [c.detail for c in checks if c.reason is Reason.status_pending][:1]
        uncertain = _pick(base.checks, Ternary.unknown, _DECISIVE_UNKNOWN)
        definite = _pick(base.checks, Ternary.true, (Reason.jurisdiction_match,))
        if definite:
            parts.append(definite[0].detail)
        if uncertain:
            parts.append(
                "If it were enacted, whether it would cover this address is not settled: "
                + uncertain[0].detail
            )

    elif decision.result is BaseResult.not_yet_effective:
        parts += [c.detail for c in checks if c.reason is Reason.before_effective_date][:1]
        uncertain = _pick(base.checks, Ternary.unknown, _DECISIVE_UNKNOWN)
        if uncertain:
            parts.append("Coverage on that date is also uncertain: " + uncertain[0].detail)

    elif decision.result is BaseResult.unknown:
        unknowns = _pick(checks, Ternary.unknown, _DECISIVE_UNKNOWN)
        parts += [c.detail for c in unknowns][:2]
        settled = _pick(base.checks, Ternary.false)
        if not unknowns and settled:
            parts += [settled[0].detail]

    else:  # applies
        geo = _pick(base.checks, Ternary.true, (Reason.jurisdiction_match,))
        time = _pick(base.checks, Ternary.true, (Reason.in_force,))
        facts = [
            c
            for c in _pick(base.checks, Ternary.true)
            if c.reason in (Reason.fact_satisfied, Reason.year_built_proxy_used)
        ]
        beaten = [c for c in base.checks if c.check == "exemption" and c.value is Ternary.false]
        unconditional = [
            c
            for c in base.checks
            if c.reason in (Reason.unconditional_reviewed, Reason.coverage_classified)
        ]
        for group in (geo, time, facts, beaten, unconditional):
            if group:
                parts.append(group[0].detail)

    if decision.conflict_flag and decision.conflict_reason:
        # Only if it is not already said. A conflicting-effective-dates flag
        # carries the same sentence as the check that raised it, and appending
        # it blindly printed that sentence twice in every explanation it
        # touched - visible in the UI, and the kind of thing that makes a
        # reader doubt the rest of the output.
        if decision.conflict_reason not in parts:
            parts.append(decision.conflict_reason)

    if (
        decision.confidence is Confidence.low
        and decision.result is not BaseResult.unknown
        and decision.confidence_reasons
    ):
        # Said in the answer itself, not only in the API view: lookups.json
        # has four fields, and this is the one a reader always sees.
        parts.append(
            "Confidence: low, for human review - "
            + "; ".join(decision.confidence_reasons[:2]).rstrip(".")
            + "."
        )

    if base.unresolved_fields and decision.result in (
        BaseResult.unknown,
        BaseResult.pending,
        BaseResult.not_yet_effective,
    ):
        missing = ", ".join(f.replace("_", " ") for f in base.unresolved_fields)
        parts.append(f"Supplying {missing} would settle it.")

    text = " ".join(p.rstrip() for p in parts if p).strip()
    return text or "No check produced a reason; treat this as unresolved."
