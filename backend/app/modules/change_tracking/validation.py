"""Submission checks for the five fixed change cases.

An empty set is only a valid answer for T5.  Missing inputs and truncated runs
must fail before a changes.json file is served.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path

REQUIRED_TEST_IDS = ("T1", "T2", "T3", "T4", "T5")


def sample_address_ids(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    ids = [(row.get("address_id") or "").strip() for row in rows]
    if len(ids) != 500 or len(set(ids)) != 500 or any(not address_id for address_id in ids):
        raise ValueError("Sample address CSV must contain 500 distinct nonempty IDs")
    return set(ids)


def validate_changes(payload: Mapping[str, object], *, address_ids: set[str]) -> list[str]:
    problems: list[str] = []
    required = set(REQUIRED_TEST_IDS)
    if set(payload) != required:
        problems.append(f"test keys are {sorted(payload)}, expected {list(REQUIRED_TEST_IDS)}")

    for test_id in REQUIRED_TEST_IDS:
        entry = payload.get(test_id)
        if not isinstance(entry, dict):
            problems.append(f"{test_id}: result is missing or not an object")
            continue
        affected = entry.get("affected_address_ids")
        conflicts = entry.get("conflict_flag_address_ids", [])
        if not isinstance(affected, list) or not all(isinstance(x, str) for x in affected):
            problems.append(f"{test_id}: affected_address_ids must be a string list")
            continue
        if not isinstance(conflicts, list) or not all(isinstance(x, str) for x in conflicts):
            problems.append(f"{test_id}: conflict_flag_address_ids must be a string list")
            continue
        if affected != sorted(set(affected)):
            problems.append(f"{test_id}: affected IDs must be sorted and unique")
        if conflicts != sorted(set(conflicts)):
            problems.append(f"{test_id}: conflict IDs must be sorted and unique")
        for field, ids in (("affected", affected), ("conflict", conflicts)):
            unknown = sorted(set(ids) - address_ids)
            if unknown:
                problems.append(f"{test_id}: unknown {field} address IDs {unknown[:5]}")
        if set(conflicts) - set(affected):
            problems.append(f"{test_id}: conflict IDs must be affected IDs")
        if not isinstance(entry.get("notes"), str) or not entry["notes"].strip():
            problems.append(f"{test_id}: notes are missing")
        if test_id != "T5" and not affected:
            problems.append(f"{test_id}: unexpectedly empty affected set")
        if test_id == "T5" and affected:
            problems.append("T5: failed measure has affected addresses")
        if test_id == "T3" and not conflicts:
            problems.append("T3: possible local-law conflicts are missing")
    return problems
