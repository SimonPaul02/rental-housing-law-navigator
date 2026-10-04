#!/usr/bin/env python
"""Replay 500 addresses after a source-backed compiler v2 rebuild.

Run after `scripts/compile_rules.py --no-model` (or its model-assisted form).
The baseline CSV is the checked-in v1 applies-to-unknown quarantine comparison.
Every former applies and every newly produced applies is exported for review.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.db.models import Document, Rule  # noqa: E402
from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402
from app.modules.address_lookup.rule_evaluation.base import evaluate_base  # noqa: E402
from app.modules.address_lookup.rule_evaluation.explanations import explain  # noqa: E402
from app.modules.address_lookup.rule_evaluation.interactions import (
    resolve_interactions,  # noqa: E402
)
from app.modules.address_lookup.rule_evaluation.predicates import (  # noqa: E402
    AddressEvidence,
    FactValue,
)


def evidence(row: dict, resolution: dict) -> AddressEvidence:
    def number(field: str) -> FactValue:
        raw = row[field]
        return FactValue(int(raw), "present") if raw and raw.isdigit() else FactValue()

    city = resolution.get("legal_city") if resolution["status"] == "resolved" else None
    state = resolution.get("legal_state") or row["state"]
    return AddressEvidence(
        address_id=row["address_id"],
        legal_city=FactValue(city, "present" if city else "not_supplied"),
        legal_state=FactValue(state, "present"),
        units=number("units"),
        year_built=number("year_built"),
        certificate_of_occupancy_date=FactValue(),
        use_code=FactValue(row["use_code"], "present" if row["use_code"] else "not_supplied"),
        jurisdiction_method=resolution.get("resolution_method"),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2026-10-04")
    parser.add_argument(
        "--output", type=Path, default=settings.data_root / "data" / "coverage_v2_replay.csv"
    )
    args = parser.parse_args()
    as_of = dt.date.fromisoformat(args.as_of)
    root = settings.data_root / "data"
    baseline_path = root / "coverage_quarantine_replay.csv"
    baseline = list(csv.DictReader(baseline_path.open(encoding="utf-8")))
    old_applies = {
        (r["address_id"], r["team_rule_id"]) for r in baseline if r["before"] == "applies"
    }
    resolutions = {
        r["address_id"]: r
        for r in json.loads((root / "resolved_addresses.json").read_text(encoding="utf-8"))
    }
    addresses = list(csv.DictReader((root / "sample_addresses.csv").open(encoding="utf-8")))
    if len(addresses) != 500 or len(resolutions) != 500:
        parser.error("expected the full 500-address sample and resolution snapshot")

    async with SessionLocal() as session:
        records = list(
            (await session.execute(select(Rule).order_by(Rule.team_rule_id))).scalars().all()
        )
        docs = await session.execute(select(Document.doc_id, Document.body))
        sources = {doc_id: body for doc_id, body in docs if body}
    compiled, relations, _ = adapters.compile_all(records, sources=sources, persist=True)
    if len(compiled) != 115:
        parser.error(f"expected 115 rules; found {len(compiled)}")

    output: list[dict] = []
    new_applies = former_unknown = 0
    for address in addresses:
        ev = evidence(address, resolutions[address["address_id"]])
        bases = [evaluate_base(rule, ev, as_of) for rule in compiled.values()]
        for decision in resolve_interactions(bases, relations):
            key = (address["address_id"], decision.team_rule_id)
            result = str(decision.result)
            was_applies = key in old_applies
            if result == "applies" and not was_applies:
                category = "new_applies"
                new_applies += 1
            elif was_applies and result == "unknown":
                category = "former_applies_unknown"
                former_unknown += 1
            elif was_applies:
                category = "former_applies_other"
            else:
                continue
            rule = compiled[decision.team_rule_id]
            if result == "applies" and not rule.is_usable:
                raise RuntimeError(f"unverified applies: {key}")
            output.append(
                {
                    "address_id": key[0],
                    "team_rule_id": key[1],
                    "baseline": "applies" if was_applies else "other",
                    "rebuilt": result,
                    "category": category,
                    "review_state": str(rule.review_state),
                    "coverage_basis": str(rule.coverage_basis),
                    "source_hash": rule.source_hash or "",
                    "pending_clauses": "; ".join(u.clause_id for u in rule.unmapped_text),
                    "explanation": explain(decision),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=list(output[0])
            if output
            else [
                "address_id",
                "team_rule_id",
                "baseline",
                "rebuilt",
                "category",
                "review_state",
                "coverage_basis",
                "source_hash",
                "pending_clauses",
                "explanation",
            ],
        )
        writer.writeheader()
        writer.writerows(output)
    print(f"replayed {len(addresses)} addresses x {len(compiled)} rules as of {as_of}")
    print(f"new applies: {new_applies}; former applies now unknown: {former_unknown}")
    print(f"review rows: {len(output)}; report: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
