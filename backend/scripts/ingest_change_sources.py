#!/usr/bin/env python
"""Capture the named Module C public sources, optionally extracting new rules.

    cd backend
    .venv/bin/python scripts/ingest_change_sources.py
    .venv/bin/python scripts/ingest_change_sources.py --extract

Only URLs in corpus/links_only.csv are eligible.  Re-run after a blocked
source is lawfully available; no block is bypassed automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Document  # noqa: E402
from app.modules.rule_extraction import service  # noqa: E402
from app.modules.rule_extraction.pipeline.ingest import (  # noqa: E402
    CHANGE_SOURCE_IDS,
    ingest_documents,
)


async def run(doc_ids: list[str], *, extract: bool, terms_reviewed: bool) -> None:
    async with SessionLocal() as session:
        results = await ingest_documents(session, doc_ids, terms_reviewed=terms_reviewed)
        await session.commit()
    print(json.dumps(results, indent=2))
    if extract:
        for doc_id in doc_ids:
            if doc_id == "D092":  # A long court summary used only for date evidence.
                continue
            async with SessionLocal() as session:
                doc = await session.get(Document, doc_id)
                if not doc or not doc.body:
                    print(f"{doc_id}: no captured body; extraction skipped")
                    continue
                outcome = await service.extract_document(session, doc)
                await session.commit()
                print(
                    f"{doc_id}: {len(outcome.rules)} verified rules, "
                    f"{outcome.spans_rejected} rejected spans"
                )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc_ids", nargs="*", default=list(CHANGE_SOURCE_IDS))
    parser.add_argument("--extract", action="store_true")
    parser.add_argument(
        "--terms-reviewed",
        action="store_true",
        help="Permit check-terms sources only after separate terms review.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.doc_ids, extract=args.extract, terms_reviewed=args.terms_reviewed))
