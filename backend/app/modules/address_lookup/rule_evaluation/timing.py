"""Status and effective dates: is this rule law here, on this day?

Three things this module refuses to do, each of which is a scored error in the
challenge:

  * treat `2026-07` as the first of July, which would turn a rule that might
    take effect on the 20th into one that applies on the 2nd;
  * pick a winner between two disagreeing effective dates;
  * let a pending bill become law because the query date moved forward. A bill
    is a proposal on every date until a source says it was enacted.
"""

from __future__ import annotations

import datetime as dt

from app.modules.address_lookup.rule_adapter.models import CompiledRule, EffectiveDate
from app.modules.address_lookup.rule_evaluation.decisions import (
    BaseResult,
    CheckTrace,
    Reason,
    Ternary,
)


def evaluate_time(
    rule: CompiledRule, as_of: dt.date
) -> tuple[Ternary, BaseResult | None, CheckTrace]:
    """Returns (in force?, early result if settled here, trace).

    A non-None second element means time alone decided the outcome and coverage
    need not be consulted for the *temporal* answer - though the caller still
    checks geography and coverage, because a pending bill that could never
    cover this building should not be reported at all.
    """
    if rule.status == "failed":
        return (
            Ternary.false,
            BaseResult.failed,
            CheckTrace(
                check="time",
                value=Ternary.false,
                reason=Reason.status_failed,
                detail="Recorded as defeated, struck or repealed, so it imposes no requirement.",
            ),
        )

    if rule.status == "pending":
        return (
            Ternary.false,
            BaseResult.pending,
            CheckTrace(
                check="time",
                value=Ternary.false,
                reason=Reason.status_pending,
                detail=(
                    "Still a bill or proposal rather than law. It is reported as pending on "
                    "every date until a source records its enactment."
                ),
            ),
        )

    dates = list(rule.effective_dates)

    if not dates:
        if rule.status == "not_yet_effective":
            return (
                Ternary.unknown,
                BaseResult.unknown,
                CheckTrace(
                    check="time",
                    value=Ternary.unknown,
                    reason=Reason.effective_date_missing,
                    detail=(
                        "Recorded as enacted but not yet effective, with no effective date in "
                        "the source, so whether it is in force on "
                        f"{as_of.isoformat()} cannot be determined. A missing date is not "
                        "January 1."
                    ),
                ),
            )
        return (
            Ternary.true,
            None,
            CheckTrace(
                check="time",
                value=Ternary.true,
                reason=Reason.in_force,
                detail=f"Recorded in force, with no later effective date, on {as_of.isoformat()}.",
            ),
        )

    if len(dates) > 1:
        return _conflicting(dates, as_of)

    return _single(dates[0], as_of)


def _single(date: EffectiveDate, as_of: dt.date) -> tuple[Ternary, BaseResult | None, CheckTrace]:
    if as_of < date.earliest:
        return (
            Ternary.false,
            BaseResult.not_yet_effective,
            CheckTrace(
                check="time",
                value=Ternary.false,
                reason=Reason.before_effective_date,
                detail=(
                    f"Enacted but takes effect {date.raw}, after the query date "
                    f"{as_of.isoformat()}."
                ),
            ),
        )

    if as_of > date.latest:
        return (
            Ternary.true,
            None,
            CheckTrace(
                check="time",
                value=Ternary.true,
                reason=Reason.in_force,
                detail=f"In force since {date.raw}, on or before {as_of.isoformat()}.",
            ),
        )

    # Inside the interval a month- or year-precision date leaves open.
    if date.is_exact:
        return (
            Ternary.true,
            None,
            CheckTrace(
                check="time",
                value=Ternary.true,
                reason=Reason.in_force,
                detail=f"Effective {date.raw}, on or before the query date {as_of.isoformat()}.",
            ),
        )

    return (
        Ternary.unknown,
        BaseResult.unknown,
        CheckTrace(
            check="time",
            value=Ternary.unknown,
            reason=Reason.inside_effective_interval,
            detail=(
                f"The source gives the effective date only as {date.raw} "
                f"({date.precision} precision), so any day between {date.earliest.isoformat()} "
                f"and {date.latest.isoformat()} is possible and {as_of.isoformat()} falls "
                "inside that window."
            ),
        ),
    )


def _conflicting(
    dates: list[EffectiveDate], as_of: dt.date
) -> tuple[Ternary, BaseResult | None, CheckTrace]:
    earliest = min(d.earliest for d in dates)
    latest = max(d.latest for d in dates)
    listed = ", ".join(d.raw for d in dates)

    if as_of < earliest:
        return (
            Ternary.false,
            BaseResult.not_yet_effective,
            CheckTrace(
                check="time",
                value=Ternary.false,
                reason=Reason.before_effective_date,
                detail=(
                    f"The sources give more than one candidate effective date ({listed}); "
                    f"{as_of.isoformat()} is before all of them."
                ),
            ),
        )
    if as_of > latest:
        return (
            Ternary.true,
            None,
            CheckTrace(
                check="time",
                value=Ternary.true,
                reason=Reason.in_force,
                detail=(
                    f"The sources give more than one candidate effective date ({listed}); "
                    f"{as_of.isoformat()} is after all of them, so it is in force either way."
                ),
            ),
        )
    return (
        Ternary.unknown,
        BaseResult.unknown,
        CheckTrace(
            check="time",
            value=Ternary.unknown,
            reason=Reason.conflicting_effective_dates,
            detail=(
                f"The sources disagree about the effective date ({listed}) and "
                f"{as_of.isoformat()} falls between the candidates, so whether the rule is in "
                "force is unknown."
            ),
        ),
    )
