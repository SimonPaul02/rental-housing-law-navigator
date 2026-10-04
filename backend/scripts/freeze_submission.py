#!/usr/bin/env python
"""Validate and freeze rules.json, lookups.json, and changes.json together.

Run after source ingestion, extraction, reviewed corrections, and Module B's
latest address pass.  This refuses to write any file until all checks pass.

--allow-incomplete relaxes one check only: changes.json may then leave out a
change case that cannot be answered yet (and keep one that is partial). It is
printed, and nothing else about rules.json or lookups.json is relaxed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.db.models import Address, Document, Rule  # noqa: E402
from app.modules.address_lookup.service import run_lookup_export  # noqa: E402
from app.modules.change_tracking import service as changes  # noqa: E402
from app.modules.change_tracking.validation import (  # noqa: E402
    REQUIRED_TEST_IDS,
    ChangesExport,
    build_changes_export,
    require_sample_address_ids,
)
from app.modules.rule_extraction.schemas import RuleRecord  # noqa: E402
from app.modules.rule_extraction.service import span_occurs_in  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(data)
        temp_name = tmp.name
    os.replace(temp_name, path)


async def build(*, allow_incomplete: bool) -> tuple[dict[str, dict], ChangesExport]:
    async with SessionLocal() as session:
        addresses = require_sample_address_ids(
            settings.addresses_csv,
            list((await session.execute(select(Address.address_id))).scalars()),
        )
        rules = list((await session.execute(select(Rule).order_by(Rule.team_rule_id))).scalars())
        documents = {d.doc_id: d for d in (await session.execute(select(Document))).scalars()}
        for rule in rules:
            doc = documents.get(rule.source_doc_id)
            if not doc or not span_occurs_in(rule.quoted_span, doc.body or ""):
                raise ValueError(f"Unverified source span for {rule.team_rule_id}")
        rule_payload = {"rules": [RuleRecord.model_validate(r).model_dump() for r in rules]}

        lookup_payload = await run_lookup_export(
            session, dt.date.fromisoformat(settings.default_as_of), write_audit=False
        )
        lookup_problems = lookup_payload.pop("validation_problems", [])
        if lookup_problems:
            raise ValueError(f"Lookup export failed: {lookup_problems[:5]}")

        tests = changes.load_tests()
        if not allow_incomplete and {t.test_id for t in tests} != set(REQUIRED_TEST_IDS):
            raise ValueError("Expected exactly T1–T5 definitions")
        results = await changes.run_tests(session, tests, address_ids=addresses)
        export = build_changes_export(results, address_ids=addresses)
        if not export.body:
            raise ValueError(f"No change case can be exported: {export.problems}")
        if not export.complete and not allow_incomplete:
            raise ValueError(
                f"Change export is incomplete: {export.problems}. "
                "Re-run with --allow-incomplete to freeze the cases that are answered."
            )

    await engine.dispose()
    return {
        "rules.json": rule_payload,
        "lookups.json": lookup_payload,
        "changes.json": export.body,
    }, export


async def main(output_dir: Path, *, allow_incomplete: bool) -> None:
    payloads, export = await build(allow_incomplete=allow_incomplete)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        _write_json(output_dir / name, payload)
    for name in payloads:
        content = (output_dir / name).read_bytes()
        print(f"{name}: sha256 {hashlib.sha256(content).hexdigest()}")
    if not export.complete:
        print("changes.json is INCOMPLETE:")
        for problem in export.problems:
            print(f"  - {problem}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Freeze changes.json without the cases that cannot be answered yet.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.output_dir, allow_incomplete=args.allow_incomplete))
