#!/usr/bin/env python
"""Replay Module B over the 500 sample addresses without a database.

Runs the same evidence builder, evaluator, interaction pass and explanations
as a live lookup, so a change to the compiler or the evaluator can be measured
before anything is deployed.

    python scripts/replay_offline.py                        # the checked-in store
    python scripts/replay_offline.py --recompile            # rebuild from data/rule_snapshot.json
    python scripts/replay_offline.py --as-of 2025-12-31 --as-of 2026-01-02
    python scripts/replay_offline.py --write-baseline /tmp/before.csv
    python scripts/replay_offline.py --baseline /tmp/before.csv --per-rule

`--recompile` never writes the store: reviews and relations are read from
data/compiled_rules.json into memory and the rules are compiled afresh there.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.modules.address_lookup import service  # noqa: E402
from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402
from app.modules.address_lookup.rule_adapter.models import HASHED_FIELDS  # noqa: E402
from app.modules.address_lookup.rule_adapter.review import (  # noqa: E402
    ReviewStore,
    compiled_from_json,
)
from app.modules.address_lookup.rule_evaluation.decisions import BaseResult  # noqa: E402

DATA = settings.data_root / "data"
_DROPPED = (BaseResult.does_not_apply, BaseResult.failed)

#: The rules behind the two date cases, found by source and category rather
#: than by id so a re-extraction does not silently empty the slice.
DATE_CASES = {
    "T1 CA AB 325": ("CA", "D022", "algorithmic_rent_setting", ("2025-12-31", "2026-01-02")),
    "T3 NJ FAIR Act": ("NJ", "D069", "algorithmic_rent_setting", ("2026-10-01", "2027-07-02")),
}


def load_snapshot() -> dict | None:
    path = DATA / "rule_snapshot.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def records_from(snapshot: dict | None, rule_ids: list[str]) -> list[SimpleNamespace]:
    if snapshot:
        return [SimpleNamespace(**row) for row in snapshot["rules"]]
    # Without a snapshot only the decision-bearing parts of the store exist;
    # Module A's own conflict flag is then unknown and treated as unset.
    return [
        SimpleNamespace(team_rule_id=rid, **{f: None for f in HASHED_FIELDS}) for rid in rule_ids
    ]


def compiled_rules(recompile: bool, snapshot: dict | None):
    store = ReviewStore.load(DATA / "compiled_rules.json")
    if not recompile:
        compiled = {
            rid: store.apply_reviews(compiled_from_json(payload))
            for rid, payload in store.revisions.items()
        }
        return records_from(snapshot, sorted(compiled)), compiled, store.all_relations()
    if not snapshot:
        raise SystemExit("--recompile needs data/rule_snapshot.json (scripts/snapshot_rules.py)")
    records = records_from(snapshot, [])
    sources = {doc_id: doc["body"] for doc_id, doc in snapshot["documents"].items()}
    proposals = adapters.load_proposals()
    suggestions = {
        r.team_rule_id: adapters.cached_suggestion(proposals, r, sources.get(r.source_doc_id))
        for r in records
    }
    store.revisions = {}  # in memory only; never saved
    compiled, relations, _ = adapters.compile_all(
        records,
        review_store=store,
        proposals={k: v for k, v in suggestions.items() if v},
        sources=sources,
        persist=False,
    )
    return records, compiled, relations


def addresses() -> list[SimpleNamespace]:
    resolutions = {
        r["address_id"]: r
        for r in json.loads((DATA / "resolved_addresses.json").read_text(encoding="utf-8"))
    }
    out = []
    with (DATA / "sample_addresses.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            res = resolutions[row["address_id"]]
            out.append(
                SimpleNamespace(
                    address_id=row["address_id"],
                    state=row["state"],
                    year_built=row["year_built"] or None,
                    units=row["units"] or None,
                    use_code=row["use_code"],
                    use_description=row["use_description"],
                    source_dataset=row["source_dataset"],
                    retrieved_at=row["retrieved_at"],
                    property_facts=None,
                    jurisdiction=SimpleNamespace(
                        method=res.get("resolution_method"),
                        legal_city=res.get("legal_city"),
                        legal_state=res.get("legal_state"),
                        note="; ".join(res.get("warnings") or []) or None,
                    ),
                )
            )
    return out


def confidence_of(decision) -> str:
    return str(getattr(decision, "confidence", None) or "-")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", action="append", default=[], help="repeatable")
    parser.add_argument("--recompile", action="store_true")
    parser.add_argument("--baseline", type=Path, help="compare with a CSV from --write-baseline")
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--per-rule", action="store_true", help="print results per rule")
    args = parser.parse_args()
    days = [dt.date.fromisoformat(d) for d in (args.as_of or [settings.default_as_of])]

    snapshot = load_snapshot()
    records, compiled, relations = compiled_rules(args.recompile, snapshot)
    sample = addresses()
    evidence = {a.address_id: service.evidence_for(a) for a in sample}
    state_of = {a.address_id: a.state for a in sample}
    case_days = {d for *_, pair in DATE_CASES.values() for d in pair}

    rows: list[tuple[str, str, str, str, str]] = []
    for day in sorted(set(days) | {dt.date.fromisoformat(d) for d in case_days}):
        for address in sample:
            for d in service.decide_for_address(
                records, compiled, relations, evidence[address.address_id], day
            ):
                rows.append(
                    (
                        address.address_id,
                        d.team_rule_id,
                        day.isoformat(),
                        str(d.result),
                        confidence_of(d),
                    )
                )

    reportable = [r for r in rows if r[3] not in {str(x) for x in _DROPPED}]
    for day in days:
        iso = day.isoformat()
        today = [r for r in reportable if r[2] == iso]
        print(f"\n== as of {iso}: {len(today)} reportable answers")
        by_result = Counter((r[3], r[4]) for r in today)
        for (result, confidence), n in sorted(by_result.items()):
            print(f"  {result:18} {confidence:5} {n:6}")
        by_state: dict[str, Counter] = defaultdict(Counter)
        for r in today:
            by_state[state_of[r[0]]][r[3]] += 1
        for state, counts in sorted(by_state.items()):
            print(f"  {state}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        without = sorted(
            {a.address_id for a in sample}
            - {r[0] for r in today if r[3] in ("applies", "superseded")}
        )
        print(f"  addresses with nothing that applies or is superseded: {len(without)}")
        if args.per_rule:
            per_rule: dict[str, Counter] = defaultdict(Counter)
            for r in today:
                per_rule[r[1]][r[3]] += 1
            for rule_id, counts in sorted(per_rule.items()):
                c = compiled[rule_id]
                print(
                    f"    {rule_id} {c.jurisdiction:18.18} {c.issue_key:24.24} "
                    + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                )

    print("\n== date cases")
    for name, (state, doc_id, category, (before, after)) in DATE_CASES.items():
        rule_ids = {
            rid
            for rid, c in compiled.items()
            if c.source_doc_id == doc_id and c.issue_key == category
        }
        for day in (before, after):
            counts = Counter(
                r[3] for r in rows if r[1] in rule_ids and r[2] == day and state_of[r[0]] == state
            )
            print(f"  {name} {day}: {dict(sorted(counts.items()))} rules={sorted(rule_ids)}")

    if args.write_baseline:
        with args.write_baseline.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["address_id", "team_rule_id", "as_of", "result", "confidence"])
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows -> {args.write_baseline}")

    if args.baseline:
        with args.baseline.open(encoding="utf-8") as handle:
            before = {
                (r["address_id"], r["team_rule_id"], r["as_of"]): r for r in csv.DictReader(handle)
            }
        changes = Counter()
        by_rule: dict[str, Counter] = defaultdict(Counter)
        for address_id, rule_id, day, result, _ in rows:
            old = before.get((address_id, rule_id, day))
            if old and old["result"] != result:
                changes[(day, old["result"], result)] += 1
                by_rule[rule_id][(old["result"], result)] += 1
        print("\n== changes against the baseline")
        for (day, old, new), n in sorted(changes.items()):
            print(f"  {day} {old:18} -> {new:18} {n:6}")
        if args.per_rule:
            for rule_id, counts in sorted(by_rule.items(), key=lambda kv: -sum(kv[1].values())):
                print(
                    f"    {rule_id} "
                    + ", ".join(f"{a}->{b} {n}" for (a, b), n in counts.most_common())
                )


if __name__ == "__main__":
    main()
