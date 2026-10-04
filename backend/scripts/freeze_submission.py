#!/usr/bin/env python
"""Validate and freeze rules.json, lookups.json, and changes.json together.

Run after source ingestion, extraction, reviewed corrections, and Module B's
latest address pass.  This refuses to write any file until all checks pass.
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
    require_sample_address_ids,
    validate_changes,
)
from app.modules.rule_extraction.schemas import RuleRecord  # noqa: E402
from app.modules.rule_extraction.service import span_occurs_in  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(data)
        temp_name = tmp.name
    os.replace(temp_name, path)


async def build() -> dict[str, dict]:
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
        if {t.test_id for t in tests} != set(REQUIRED_TEST_IDS):
            raise ValueError("Expected exactly T1–T5 definitions")
        results = [await changes.run_test(session, t, persist=False) for t in tests]
        change_payload = {
            r.test_id: {
                "affected_address_ids": r.affected_address_ids,
                "notes": r.notes,
                **(
                    {"conflict_flag_address_ids": r.conflict_flag_address_ids}
                    if r.conflict_flag_address_ids
                    else {}
                ),
            }
            for r in results
        }
        problems = validate_changes(change_payload, address_ids=addresses)
        if problems:
            raise ValueError(f"Change export failed: {problems}")

    await engine.dispose()
    return {
        "rules.json": rule_payload,
        "lookups.json": lookup_payload,
        "changes.json": change_payload,
    }


async def main(output_dir: Path) -> None:
    payloads = await build()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        _write_json(output_dir / name, payload)
    for name in payloads:
        content = (output_dir / name).read_bytes()
        print(f"{name}: sha256 {hashlib.sha256(content).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    asyncio.run(main(args.output_dir))
