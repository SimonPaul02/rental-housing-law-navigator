#!/usr/bin/env python
"""Inspect or resolve an ambiguous effective date using current source text.

Decision JSON: team_rule_id, reviewer, rationale, effective_dates (a list of
{raw, source_span}), and optional key_value_period ({start, end, source_span}).
An empty effective_dates list explicitly confirms no rule-effective date;
include role_evidence_span when clearing an ambiguous date without a value period.
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


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rule-id")
    group.add_argument("--decision", type=Path)
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text(encoding="utf-8")) if args.decision else None
    rule_id = args.rule_id or decision["team_rule_id"]

    async with SessionLocal() as session:
        record = (
            await session.execute(
                select(Rule)
                .options(selectinload(Rule.document))
                .where(Rule.team_rule_id == rule_id)
            )
        ).scalar_one_or_none()
        if record is None or not record.document or not record.document.body:
            parser.error("rule or current source body is unavailable")
        source = record.document.body
    compiled, _ = compile_rule(record, source_text=source)
    store = adapters.store()
    if decision is None:
        print(
            json.dumps(
                {
                    "team_rule_id": rule_id,
                    "rule_version_hash": compiled.rule_version_hash,
                    "source_hash": compiled.source_hash,
                    "compiler_version": compiled.compiler_version,
                    "effective_date_unresolved": compiled.effective_date_unresolved,
                    "effective_dates": [d.to_json() for d in compiled.effective_dates],
                    "key_value_period": compiled.key_value_period.to_json()
                    if compiled.key_value_period
                    else None,
                    "notes": compiled.notes,
                    "current_review": store.date_reviews.get(rule_id),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    for key in ("rule_version_hash", "source_hash", "compiler_version"):
        if decision.get(key) != getattr(compiled, key):
            parser.error(f"stale {key}; inspect the current compilation first")
    store.review_dates(
        compiled,
        source_text=source,
        reviewer=decision["reviewer"],
        rationale=decision["rationale"],
        effective_dates=decision["effective_dates"],
        key_value_period=decision.get("key_value_period"),
        role_evidence_span=decision.get("role_evidence_span"),
    )
    store.save()
    print(f"recorded date review for {rule_id}")


if __name__ == "__main__":
    asyncio.run(main())
