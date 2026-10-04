#!/usr/bin/env python
"""The review step: approve compiled relations whose evidence is explicit.

An unreviewed relation supersedes nothing, so without this step the evaluator
reports a state and a city rule as both applying even where the state rule
itself says it steps aside. Approval is recorded per relation with the span
that evidences it, and it is recorded in the store rather than in code, so a
later reviewer can read what was approved and overturn it.

Only these evidence forms are accepted. Each is a sentence the rule itself
states about its own reach - not an inference about which level of government
ought to win:

  yields_to    "does not apply to property subject to a local ..."  (AB 1482)
               "housing under a local rent control ..."
               "preempts" / "preempted by"
  both_apply   "in addition to"

    python scripts/review_rules.py            # show what would be approved
    python scripts/review_rules.py --approve  # record the approvals
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402

#: Evidence form -> why it settles the question, recorded with the approval.
ACCEPTED: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"does not apply (?:to|where)[^.;]{0,90}?\b(?:local|municipal|city)\b", re.I),
        "yields_to",
        "the rule states it does not reach property a local ordinance already covers",
    ),
    (
        re.compile(r"(?:housing|units?|property) (?:under|subject to) (?:a |any )?local", re.I),
        "yields_to",
        "the rule carves out housing already under local regulation of the same issue",
    ),
    (
        re.compile(r"\bpreempt(?:s|ed)?\b", re.I),
        "yields_to",
        "the rule states that the other level of government preempts it",
    ),
    (
        re.compile(r"in addition to", re.I),
        "both_apply",
        "the rule states its requirements are cumulative with the other level's",
    ),
]


def judge(relation: dict) -> tuple[bool, str]:
    span = relation.get("source_span") or ""
    condition = relation.get("condition") or ""
    haystack = f"{span} {condition}"
    for pattern, kind, why in ACCEPTED:
        if relation["relation"] == kind and pattern.search(haystack):
            return True, why
    return False, "no accepted evidence form in the cited span"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve", action="store_true", help="record the approvals")
    args = parser.parse_args()

    store = adapters.store()
    approved = held = 0

    for relation in store.relations:
        if relation.get("review_state") == "approved":
            approved += 1
            continue
        ok, why = judge(relation)
        arrow = f"{relation['left_rule_id']} -> {relation['right_rule_id']}"
        if ok:
            approved += 1
            print(f"  approve  {relation['relation']:11} {arrow}  ({why})")
            if args.approve:
                relation["review_state"] = "approved"
                relation["review_note"] = why
        else:
            held += 1
            print(f"  hold     {relation['relation']:11} {arrow}  ({why})")

    print(f"\n{approved} approved, {held} held for a human")
    if args.approve:
        store.save()
        print("recorded in", adapters.STORE_PATH)
    else:
        print("dry run - pass --approve to record")


if __name__ == "__main__":
    main()
