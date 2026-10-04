#!/usr/bin/env python
"""Freeze the Module A rules and their source bodies for offline replay.

Reads the database only. The snapshot holds every field a compilation is
bound to (`HASHED_FIELDS`) and the body of each document a rule cites, plus
the change-case evidence documents, so `replay_offline.py --recompile` can
rebuild the compiled store exactly as `compile_rules.py` would.

    python scripts/snapshot_rules.py                 # -> data/rule_snapshot.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Document, Rule  # noqa: E402
from app.modules.address_lookup.rule_adapter.models import HASHED_FIELDS  # noqa: E402

#: Evidence documents no rule cites but a date review may rely on.
EVIDENCE_DOCS = ("D092",)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=settings.data_root / "data" / "rule_snapshot.json"
    )
    args = parser.parse_args()

    async with SessionLocal() as session:
        rules = list((await session.execute(select(Rule).order_by(Rule.team_rule_id))).scalars())
        wanted = {r.source_doc_id for r in rules if r.source_doc_id} | set(EVIDENCE_DOCS)
        documents = list(
            (await session.execute(select(Document).where(Document.doc_id.in_(wanted)))).scalars()
        )
    await engine.dispose()

    payload = {
        "rules": [
            {"team_rule_id": r.team_rule_id, **{f: getattr(r, f) for f in HASHED_FIELDS}}
            for r in rules
        ],
        "documents": {
            d.doc_id: {"url": d.url, "content_hash": d.content_hash, "body": d.body}
            for d in sorted(documents, key=lambda d: d.doc_id)
            if d.body
        },
    }
    args.output.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    missing = sorted(wanted - set(payload["documents"]))
    print(f"{len(payload['rules'])} rules, {len(payload['documents'])} documents -> {args.output}")
    if missing:
        print(f"no stored body for: {', '.join(missing)}")


if __name__ == "__main__":
    asyncio.run(main())
