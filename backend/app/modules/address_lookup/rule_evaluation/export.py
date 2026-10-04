"""Map internal decisions to lookups.json, and refuse to ship a bad one.

Two separate jobs. `to_submission` applies one documented precedence policy so
the UI and the export can never disagree about what a decision means.
`validate_submission` then re-reads the output as a stranger would and fails
loudly: the export is the deliverable, and a silently truncated one is worse
than a crash.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.modules.address_lookup.rule_evaluation.decisions import (
    SUBMISSION_RESULTS,
    BaseResult,
    Decision,
)

#: The single precedence policy. Internal result on the left, what the
#: challenge is told on the right; None means the rule is not reported for
#: that address at all.
SUBMISSION_MAPPING: dict[BaseResult, str | None] = {
    BaseResult.does_not_apply: None,  # definitely outside geography/coverage, or exempt
    BaseResult.failed: None,  # a defeated measure is not law anywhere
    BaseResult.pending: "pending",
    BaseResult.not_yet_effective: "not_yet_effective",
    BaseResult.unknown: "unknown",
    BaseResult.superseded: "superseded",
    BaseResult.applies: "applies",
}


def to_submission(
    decisions: list[Decision], all_address_ids: list[str], as_of: dt.date
) -> dict[str, Any]:
    """Build the whole file from one completed run.

    Every supplied address id gets a key, including the ones with nothing to
    report: an absent address is indistinguishable from an address we failed to
    evaluate, and the challenge asks for all five hundred.
    """
    lookups: dict[str, list[dict[str, Any]]] = {aid: [] for aid in all_address_ids}
    seen: set[tuple[str, str]] = set()

    for decision in decisions:
        reported = SUBMISSION_MAPPING[decision.result]
        if reported is None:
            continue
        key = (decision.address_id, decision.team_rule_id)
        if key in seen:
            continue
        seen.add(key)
        lookups.setdefault(decision.address_id, []).append(
            {
                "team_rule_id": decision.team_rule_id,
                "result": reported,
                "explanation": decision.explanation,
                "conflict_flag": bool(decision.conflict_flag),
            }
        )

    # Deterministic ordering, so an offline replay is byte-identical.
    for entries in lookups.values():
        entries.sort(key=lambda e: e["team_rule_id"])

    return {
        "as_of": as_of.isoformat(),
        "lookups": {aid: lookups[aid] for aid in sorted(lookups)},
    }


class SubmissionInvalid(ValueError):
    """The export is not shippable, with every reason listed."""


def validate_submission(
    payload: dict[str, Any],
    *,
    expected_address_ids: list[str],
    known_rule_ids: set[str],
) -> list[str]:
    """Return the problems. Empty means shippable."""
    problems: list[str] = []

    if set(payload) != {"as_of", "lookups"}:
        problems.append(f"top level keys are {sorted(payload)}, expected as_of and lookups")
    lookups = payload.get("lookups")
    if not isinstance(lookups, dict):
        return problems + ["lookups is not an object"]

    expected = set(expected_address_ids)
    actual = set(lookups)
    if missing := sorted(expected - actual):
        problems.append(f"{len(missing)} supplied address ids are absent, e.g. {missing[:5]}")
    if extra := sorted(actual - expected):
        problems.append(
            f"{len(extra)} address ids are not in the supplied sample, e.g. {extra[:5]}"
        )

    for address_id, entries in lookups.items():
        if not isinstance(entries, list):
            problems.append(f"{address_id}: value is not a list")
            continue
        per_rule: dict[str, int] = {}
        for entry in entries:
            rule_id = entry.get("team_rule_id")
            result = entry.get("result")
            explanation = entry.get("explanation")
            flag = entry.get("conflict_flag")

            if set(entry) != {"team_rule_id", "result", "explanation", "conflict_flag"}:
                problems.append(f"{address_id}/{rule_id}: unexpected fields {sorted(entry)}")
            if rule_id not in known_rule_ids:
                problems.append(
                    f"{address_id}: team_rule_id {rule_id!r} is not in this run's rules"
                )
            if result not in SUBMISSION_RESULTS:
                problems.append(
                    f"{address_id}/{rule_id}: result {result!r} is not a submission value"
                )
            if not isinstance(explanation, str) or not explanation.strip():
                problems.append(f"{address_id}/{rule_id}: explanation is empty")
            if not isinstance(flag, bool):
                problems.append(f"{address_id}/{rule_id}: conflict_flag is not a boolean")
            per_rule[rule_id] = per_rule.get(rule_id, 0) + 1

        for rule_id, count in per_rule.items():
            if count > 1:
                problems.append(f"{address_id}: {rule_id} reported {count} times")

    return problems
