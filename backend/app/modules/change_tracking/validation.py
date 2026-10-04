"""Submission checks for the five fixed change cases.

An empty set is only a valid answer for T5.  Missing inputs and truncated runs
must fail before a changes.json file is served.

Problems come in two kinds, judged per case so one bad case cannot take the
other four down with it:

  invalid     the case's entry cannot be exported at all (a malformed list, an
              address outside the sample, an empty set where the case expects
              addresses).
  incomplete  the entry can be exported, but something the case also asks for
              is missing (T3 without conflict flags, a case that did not run).

The frozen submission refuses both. The everyday export leaves out the invalid
cases, keeps the incomplete ones, and says which is which.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.change_tracking.schemas import ChangeTestResult

REQUIRED_TEST_IDS = ("T1", "T2", "T3", "T4", "T5")


def sample_address_ids(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    ids = [(row.get("address_id") or "").strip() for row in rows]
    if len(ids) != 500 or len(set(ids)) != 500 or any(not address_id for address_id in ids):
        raise ValueError("Sample address CSV must contain 500 distinct nonempty IDs")
    return set(ids)


def require_sample_address_ids(path: Path, database_ids: list[str]) -> set[str]:
    """Require the database import to match the canonical 500 IDs exactly."""
    expected = sample_address_ids(path)
    actual = set(database_ids)
    if len(database_ids) != len(expected) or actual != expected:
        raise ValueError(
            "Database addresses differ from the 500 supplied sample IDs: "
            f"missing={sorted(expected - actual)[:5]}, "
            f"extra={sorted(actual - expected)[:5]}"
        )
    return expected


@dataclass
class ChangeCheck:
    invalid: dict[str, list[str]] = field(default_factory=dict)
    incomplete: dict[str, list[str]] = field(default_factory=dict)

    @property
    def problems(self) -> list[str]:
        return [
            problem
            for kind in (self.invalid, self.incomplete)
            for test_id in sorted(kind)
            for problem in kind[test_id]
        ]


def check_entry(
    test_id: str, entry: object, *, address_ids: set[str]
) -> tuple[list[str], list[str]]:
    """One case's export entry: (invalid problems, incomplete problems)."""
    if not isinstance(entry, dict):
        return [f"{test_id}: result is missing or not an object"], []
    affected = entry.get("affected_address_ids")
    conflicts = entry.get("conflict_flag_address_ids", [])
    if not isinstance(affected, list) or not all(isinstance(x, str) for x in affected):
        return [f"{test_id}: affected_address_ids must be a string list"], []
    if not isinstance(conflicts, list) or not all(isinstance(x, str) for x in conflicts):
        return [f"{test_id}: conflict_flag_address_ids must be a string list"], []

    invalid: list[str] = []
    incomplete: list[str] = []
    if affected != sorted(set(affected)):
        invalid.append(f"{test_id}: affected IDs must be sorted and unique")
    if conflicts != sorted(set(conflicts)):
        invalid.append(f"{test_id}: conflict IDs must be sorted and unique")
    for kind, ids in (("affected", affected), ("conflict", conflicts)):
        unknown = sorted(set(ids) - address_ids)
        if unknown:
            invalid.append(f"{test_id}: unknown {kind} address IDs {unknown[:5]}")
    if set(conflicts) - set(affected):
        invalid.append(f"{test_id}: conflict IDs must be affected IDs")
    if not isinstance(entry.get("notes"), str) or not entry["notes"].strip():
        invalid.append(f"{test_id}: notes are missing")
    if test_id != "T5" and not affected:
        invalid.append(
            f"{test_id}: unexpectedly empty affected set - only T5 may export none, "
            "so an input is missing or wrong rather than this being a finding"
        )
    if test_id == "T5" and affected:
        invalid.append("T5: failed measure has affected addresses")
    if test_id == "T3" and not conflicts:
        incomplete.append("T3: possible local-law conflicts are missing")
    return invalid, incomplete


def check_changes(payload: Mapping[str, object], *, address_ids: set[str]) -> ChangeCheck:
    check = ChangeCheck()
    for test_id in sorted(set(payload) | set(REQUIRED_TEST_IDS)):
        if test_id not in payload:
            check.incomplete[test_id] = [f"{test_id}: result is missing or not an object"]
            continue
        invalid, incomplete = check_entry(test_id, payload[test_id], address_ids=address_ids)
        if test_id not in REQUIRED_TEST_IDS:
            incomplete.insert(0, f"{test_id}: not one of the supplied cases {REQUIRED_TEST_IDS}")
        if invalid:
            check.invalid[test_id] = invalid
        if incomplete:
            check.incomplete[test_id] = incomplete
    return check


def validate_changes(payload: Mapping[str, object], *, address_ids: set[str]) -> list[str]:
    """Every problem of either kind - the bar the frozen submission must clear."""
    return check_changes(payload, address_ids=address_ids).problems


def export_entry(result: ChangeTestResult) -> dict:
    """One case in the submission_templates/changes.json shape."""
    entry: dict = {
        "affected_address_ids": result.affected_address_ids,
        "notes": result.notes,
    }
    if result.conflict_flag_address_ids:
        entry["conflict_flag_address_ids"] = result.conflict_flag_address_ids
    return entry


@dataclass
class ChangesExport:
    body: dict[str, dict]
    #: Cases left out of `body`, and why. A left-out case is not an empty one:
    #: an empty list would claim no address is affected.
    omitted: dict[str, str]
    check: ChangeCheck

    @property
    def complete(self) -> bool:
        return not self.omitted and not self.check.incomplete

    @property
    def problems(self) -> list[str]:
        omitted = [
            f"{test_id}: not exported - {why}" for test_id, why in sorted(self.omitted.items())
        ]
        incomplete = [
            problem
            for test_id in sorted(self.check.incomplete)
            for problem in self.check.incomplete[test_id]
        ]
        return omitted + incomplete


def build_changes_export(
    results: Iterable[ChangeTestResult], *, address_ids: set[str]
) -> ChangesExport:
    """The cases that can be exported, and an account of the ones that cannot."""
    omitted: dict[str, str] = {}
    body: dict[str, dict] = {}
    partial: dict[str, list[str]] = {}
    for result in results:
        if result.status == "blocked":
            omitted[result.test_id] = result.blocked_reason or "blocked"
            continue
        body[result.test_id] = export_entry(result)
        if result.status == "partial":
            prefix = f"{result.test_id}: "
            partial[result.test_id] = [
                w if w.startswith(prefix) else prefix + w for w in result.warnings
            ] or [f"{prefix}partly answered"]

    check = check_changes(body, address_ids=address_ids)
    # A case the replay itself called partial is incomplete whether or not its
    # entry happens to pass the shape checks - e.g. T5 resting on the rent-cap
    # check alone, without its failed-measure record.
    for test_id, warnings in partial.items():
        known = check.incomplete.setdefault(test_id, [])
        known.extend(w for w in warnings if w not in known)
    for test_id, problems in check.invalid.items():
        omitted[test_id] = "; ".join(problems)
        body.pop(test_id, None)
    # A case already accounted for as omitted needs no second "missing" line.
    for test_id in omitted:
        check.incomplete.pop(test_id, None)
    return ChangesExport(body=body, omitted=omitted, check=check)
