#!/usr/bin/env python
"""Inspect relations or record an explicit, source-backed decision.

Decision JSON: left_rule_id, right_rule_id, issue_key, reviewer, rationale,
qualification (confirmed/excluded/unresolved), left_evidence_span,
right_evidence_span, and optionally valid_from (YYYY-MM-DD).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.db.models import Document, Rule  # noqa: E402
from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402
from app.modules.address_lookup.rule_adapter.models import Qualification  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", type=Path)
    args = parser.parse_args()

    async with SessionLocal() as session:
        records = list((await session.execute(select(Rule))).scalars())
        docs = await session.execute(select(Document.doc_id, Document.body))
        sources = {doc_id: body for doc_id, body in docs if body}
    store = adapters.store()
    adapters.compile_all(records, review_store=store, sources=sources, persist=False)

    if not args.decision:
        print(json.dumps(store.relations, indent=2, ensure_ascii=False))
        return

    decision = json.loads(args.decision.read_text(encoding="utf-8"))
    by_id = {r.team_rule_id: r for r in records}
    left = by_id[decision["left_rule_id"]]
    right = by_id[decision["right_rule_id"]]
    ok = store.review_relation(
        left.team_rule_id,
        right.team_rule_id,
        decision["issue_key"],
        left_source_text=sources[left.source_doc_id],
        right_source_text=sources[right.source_doc_id],
        reviewer=decision["reviewer"],
        rationale=decision["rationale"],
        qualification=Qualification(decision["qualification"]),
        left_evidence_span=decision["left_evidence_span"],
        right_evidence_span=decision["right_evidence_span"],
        valid_from=dt.date.fromisoformat(decision["valid_from"])
        if decision.get("valid_from")
        else None,
    )
    if not ok:
        parser.error("relationship is not in the current compilation")
    store.save()
    print("recorded source-backed relationship decision")


if __name__ == "__main__":
    asyncio.run(main())
