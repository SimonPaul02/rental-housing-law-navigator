#!/usr/bin/env python
"""Inspect or resolve an ambiguous effective date using current source text.

Decision JSON: team_rule_id, reviewer, rationale, effective_dates (a list of
{raw, source_span}), and optional key_value_period ({start, end, source_span}).
An empty effective_dates list explicitly confirms no rule-effective date;
include role_evidence_span when clearing an ambiguous date without a value period.

A date stated only in another captured document may be quoted from it: add
evidence_doc_id (e.g. "D092") and act_markers (e.g. ["AB 325", "338"]), each of
which must appear both in the quoted passage and in the rule's own source.
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
from app.db.models import Document, Rule  # noqa: E402
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
        evidence_text = None
        if decision and decision.get("evidence_doc_id"):
            evidence = await session.get(Document, decision["evidence_doc_id"])
            if evidence is None or not evidence.body:
                parser.error(f"{decision['evidence_doc_id']} has no stored body")
            evidence_text = evidence.body
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
                    "unverified_dates": [d.to_json() for d in compiled.unverified_dates],
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
        evidence_doc_id=decision.get("evidence_doc_id"),
        evidence_text=evidence_text,
        act_markers=tuple(decision.get("act_markers", ())),
    )
    store.save()
    print(f"recorded date review for {rule_id}")


if __name__ == "__main__":
    asyncio.run(main())
