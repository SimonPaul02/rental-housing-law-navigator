#!/usr/bin/env python
"""Compile Module A's rules into the checkable form Module B evaluates.

Run once after an extraction pass. Deterministic patterns do most of it; the
model is asked only about clauses no pattern matched, and its answers are
cached by rule version, so a re-run costs nothing unless the rule text changed.

    python scripts/compile_rules.py              # compile, propose, persist
    python scripts/compile_rules.py --no-model   # deterministic only
    python scripts/compile_rules.py --queue      # show what needs review
    python scripts/compile_rules.py --limit 5    # propose for a few rules only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.db.models import Document, Rule  # noqa: E402
from app.modules.address_lookup.rule_adapter import adapters  # noqa: E402
from app.modules.address_lookup.rule_adapter.compiler import content_hash  # noqa: E402
from app.modules.address_lookup.rule_adapter.models import rule_version_hash  # noqa: E402


async def load_rules() -> tuple[list[Rule], dict[str, str]]:
    async with SessionLocal() as session:
        rows = await session.execute(select(Rule).order_by(Rule.team_rule_id))
        docs = await session.execute(select(Document.doc_id, Document.body))
        return list(rows.scalars().all()), {doc_id: body for doc_id, body in docs if body}


def proposal_key(record: Rule, source_text: str | None = None) -> str:
    from app.core.config import settings

    return "|".join(
        [
            record.team_rule_id,
            rule_version_hash(record),
            content_hash(source_text) or "missing-source",
            settings.extraction_model,
            adapters.PROMPT_VERSION,
        ]
    )


def cached_suggestion(
    proposals: dict[str, dict], record: Rule, source_text: str | None
) -> dict | None:
    """Old model output is only a suggestion; the current compiler revalidates it."""
    current = proposals.get(proposal_key(record, source_text))
    if current is not None:
        return current
    return next(
        (
            value
            for key, value in proposals.items()
            if key.startswith(f"{record.team_rule_id}|") and isinstance(value, dict)
        ),
        None,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-model", action="store_true", help="deterministic patterns only")
    parser.add_argument("--queue", action="store_true", help="print the review queue and exit")
    parser.add_argument("--limit", type=int, default=0, help="propose for at most N rules")
    args = parser.parse_args()

    records, sources = await load_rules()
    print(f"rules: {len(records)}")

    if args.queue:
        store = adapters.store()
        print("summary:", adapters.unreviewed_summary(store))
        for item in store.queue()[:40]:
            if "relation" in item:
                rel = item["relation"]
                print(
                    f"  relation {rel['left_rule_id']} -> "
                    f"{rel['right_rule_id']} ({rel['relation']})"
                )
                continue
            count = item["unmapped_count"]
            print(f"  {item['team_rule_id']} {item['jurisdiction']}: {count} clause(s)")
            for clause in item["unmapped_text"][:3]:
                print(f"      ({clause['origin']}) {clause['text'][:110]}")
        return

    # Pass one: deterministic only, to find out what actually needs asking.
    compiled, _, _ = adapters.compile_all(records, sources=sources, persist=False)
    needing = [r for r in records if compiled[r.team_rule_id].has_unmapped]
    print(
        f"deterministic pass: {len(records) - len(needing)} fully translated, "
        f"{len(needing)} need proposals"
    )

    proposals = adapters.load_proposals()
    if not args.no_model and needing:
        targets = needing[: args.limit] if args.limit else needing
        todo = [
            r for r in targets if proposal_key(r, sources.get(r.source_doc_id)) not in proposals
        ]
        cached = len(targets) - len(todo)
        print(f"asking the model about {len(todo)} rule(s) ({cached} already cached)")

        semaphore = asyncio.Semaphore(6)

        async def one(record: Rule) -> None:
            async with semaphore:
                texts = [
                    f"{u.clause_id}: {u.text}" for u in compiled[record.team_rule_id].unmapped_text
                ]
                try:
                    proposals[
                        proposal_key(record, sources.get(record.source_doc_id))
                    ] = await adapters.propose_for(record, texts, sources.get(record.source_doc_id))
                except Exception as exc:  # noqa: BLE001 - one rule must not stop the pass
                    print(f"  {record.team_rule_id}: proposal failed ({type(exc).__name__}: {exc})")

        done = 0
        for chunk_start in range(0, len(todo), 24):
            chunk = todo[chunk_start : chunk_start + 24]
            await asyncio.gather(*(one(r) for r in chunk))
            done += len(chunk)
            adapters.save_proposals(proposals)
            print(f"  {done}/{len(todo)} proposed")

    # Pass two: compile for real, folding in every validated proposal.
    by_key = {
        r.team_rule_id: cached_suggestion(proposals, r, sources.get(r.source_doc_id))
        for r in records
    }
    store = adapters.store()
    legacy_meta = {
        rule_id: {
            key: payload[key]
            for key in ("legacy_review_state", "legacy_coverage_empty", "legacy_priority")
            if key in payload
        }
        for rule_id, payload in store.revisions.items()
    }
    store.revisions = {}  # recompile from scratch; reviews and relations are kept
    compiled, relations, _ = adapters.compile_all(
        records,
        review_store=store,
        proposals={k: v for k, v in by_key.items() if v},
        sources=sources,
        persist=True,
    )
    for rule_id, metadata in legacy_meta.items():
        if rule_id in store.revisions:
            store.revisions[rule_id].update(metadata)
    store.save()

    unmapped = [r for r in compiled.values() if r.has_unmapped]
    atoms = sum(
        len(list(r.coverage.atoms())) + len(list(r.exemptions.atoms())) for r in compiled.values()
    )
    print(f"\ncompiled {len(compiled)} rules: {atoms} conditions, {len(relations)} relations")
    print(f"  fully translated: {len(compiled) - len(unmapped)}")
    print(f"  still needing review: {len(unmapped)}")
    print("summary:", adapters.unreviewed_summary(adapters.store()))


if __name__ == "__main__":
    asyncio.run(main())
