#!/usr/bin/env python
"""Inspect and record human coverage decisions.

    python scripts/review_coverage.py --rule-id r-...
    python scripts/review_coverage.py --decision decision.json

The decision JSON names team_rule_id, rule_version_hash, source_hash,
compiler_version, reviewer, rationale, resolved_clause_ids, and optionally
coverage, exemptions, coverage_basis. The rule and document must still be
present in the current database; stale decisions are rejected.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.db.models import Rule  # noqa: E402
from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402
from app.modules.address_lookup.rule_adapter.compiler import compile_rule  # noqa: E402
from app.modules.address_lookup.rule_adapter.review import compiled_from_json  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rule-id")
    group.add_argument("--decision", type=Path)
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text(encoding="utf-8")) if args.decision else None
    rule_id = args.rule_id or decision["team_rule_id"]

    async with SessionLocal() as session:
        row = await session.execute(
            select(Rule).options(selectinload(Rule.document)).where(Rule.team_rule_id == rule_id)
        )
        record = row.scalar_one_or_none()
        if record is None:
            parser.error(f"unknown rule {rule_id}")
        source_text = record.document.body if record.document else None
        if not source_text:
            parser.error(f"source document body is unavailable for {rule_id}")
        compiled, _ = compile_rule(record, source_text=source_text)
        store = adapters.store()
        current = store.get(rule_id, compiled.rule_version_hash, compiled.source_hash)

        if decision is None:
            effective = store.apply_reviews(compiled_from_json(compiled.to_json()))
            print(
                json.dumps(
                    {
                        "team_rule_id": rule_id,
                        "rule_version_hash": compiled.rule_version_hash,
                        "source_hash": compiled.source_hash,
                        "compiler_version": compiled.compiler_version,
                        "coverage_basis": str(compiled.coverage_basis),
                        "coverage": compiled.coverage.to_json(),
                        "exemptions": compiled.exemptions.to_json(),
                        "pending_clauses": [
                            u.to_json()
                            for u in (
                                current.unmapped_text
                                if current and current.has_unmapped
                                else compiled.unmapped_text
                            )
                        ],
                        "current_review": store.review_for(
                            rule_id, compiled.rule_version_hash, compiled.source_hash
                        ),
                        "resulting_coverage": effective.coverage.to_json(),
                        "resulting_exemptions": effective.exemptions.to_json(),
                        "resulting_coverage_basis": str(effective.coverage_basis),
                        "resulting_review_state": str(effective.review_state),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        for key in ("rule_version_hash", "source_hash", "compiler_version"):
            if decision.get(key) != getattr(compiled, key):
                parser.error(f"stale {key}; inspect the current compilation first")
        store.review_coverage(
            compiled,
            source_text=source_text,
            reviewer=decision["reviewer"],
            rationale=decision["rationale"],
            resolved_clause_ids=decision.get("resolved_clause_ids", []),
            decision=decision.get("decision", "approve"),
            coverage=decision.get("coverage"),
            exemptions=decision.get("exemptions"),
            coverage_basis=decision.get("coverage_basis"),
            scope_evidence_span=decision.get("scope_evidence_span"),
        )
        store.put(compiled)
        store.save()
        print(f"recorded review for {rule_id}")


if __name__ == "__main__":
    asyncio.run(main())
