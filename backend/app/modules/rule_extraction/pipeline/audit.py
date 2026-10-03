"""Append-only audit trail for the Module A pipeline.

The spec asks for a directory of CSV/JSONL files. We store entries in one
append-only table instead and render those files on demand: same content, but
it survives a container restart, it is queryable from the API, and concurrent
pipeline stages cannot corrupt each other's writes the way parallel appends to
a CSV would.

Nothing here is ever updated or deleted. A correction is a new entry.
"""

from __future__ import annotations

import csv
import io
import json
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEntry


class Channel(StrEnum):
    """One per file in the spec's audit/ directory."""

    doc_types = "doc_types"
    fetch_log = "fetch_log"
    extraction_log = "extraction_log"
    dropped_candidates = "dropped_candidates"
    merge_log = "merge_log"
    conflicts = "conflicts"
    negative_findings = "negative_findings"
    coverage_gaps = "coverage_gaps"
    precedence_unknown = "precedence_unknown"
    runs = "runs"


# Column order per channel, so an export is stable and diffable between runs.
COLUMNS: dict[Channel, list[str]] = {
    Channel.doc_types: ["doc_id", "doc_type", "tier", "origin", "primary", "reason"],
    Channel.fetch_log: ["url", "status", "bytes", "sha256", "outcome", "reason"],
    Channel.extraction_log: [],  # JSONL - raw model output, no fixed columns
    Channel.dropped_candidates: ["doc_id", "stage", "reason", "citation", "quoted_span"],
    Channel.merge_log: ["group_key", "team_rule_id", "main_doc_id", "tier", "other_sources"],
    Channel.conflicts: ["team_rule_id", "field", "main_value", "other_values", "note"],
    Channel.negative_findings: ["jurisdiction", "category", "finding", "evidence"],
    Channel.coverage_gaps: ["doc_id", "jurisdiction", "reason"],
    Channel.precedence_unknown: ["rule_a", "rule_b", "reason"],
    Channel.runs: [
        "run_id",
        "started_at",
        "finished_at",
        "docs",
        "rules",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "status",
    ],
}


async def record(
    session: AsyncSession,
    run_id: str | None,
    channel: Channel,
    payload: dict[str, Any],
) -> None:
    """Append one entry. Never raises on a serialisation problem - losing the
    audit line must not take down the pipeline step it is describing."""
    try:
        json.dumps(payload, default=str)
    except (TypeError, ValueError):
        payload = {"unserialisable": repr(payload)[:2000]}
    session.add(AuditEntry(run_id=run_id, channel=channel.value, payload=payload))


async def record_many(
    session: AsyncSession,
    run_id: str | None,
    channel: Channel,
    payloads: list[dict[str, Any]],
) -> None:
    for payload in payloads:
        await record(session, run_id, channel, payload)


async def read(
    session: AsyncSession,
    channel: Channel,
    *,
    run_id: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    stmt = select(AuditEntry).where(AuditEntry.channel == channel.value)
    if run_id:
        stmt = stmt.where(AuditEntry.run_id == run_id)
    stmt = stmt.order_by(AuditEntry.id).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [{"ts": r.created_at.isoformat(), "run_id": r.run_id, **r.payload} for r in rows]


async def export(
    session: AsyncSession, channel: Channel, *, run_id: str | None = None
) -> tuple[str, str]:
    """Render a channel as the file the spec names. Returns (filename, body)."""
    rows = await read(session, channel, run_id=run_id)

    if channel is Channel.extraction_log:
        body = "\n".join(json.dumps(r, default=str, ensure_ascii=False) for r in rows)
        return f"{channel.value}.jsonl", body + ("\n" if body else "")

    columns = ["ts", "run_id", *COLUMNS[channel]]
    # Surface any extra keys rather than silently dropping them.
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _flatten(v) for k, v in row.items()})
    return f"{channel.value}.csv", buf.getvalue()


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)
