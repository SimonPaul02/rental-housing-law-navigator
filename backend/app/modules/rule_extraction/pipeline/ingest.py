"""Bounded, auditable capture of manifest-listed link-only documents.

The caller selects document IDs.  A source blocked by robots, terms review,
HTTP, or an interstitial stays a coverage gap; it never becomes an empty rule.
Fetched publisher text remains in the database, outside the Git corpus.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document
from app.modules.rule_extraction.pipeline import audit, inventory, links
from app.modules.rule_extraction.pipeline.fetch import USER_AGENT, FetchBot

CHANGE_SOURCE_IDS = ("D088", "D089", "D090", "D091", "D092", "D093")


async def ingest_documents(
    session: AsyncSession,
    doc_ids: list[str],
    *,
    terms_reviewed: bool = False,
) -> list[dict]:
    allowed = {row["doc_id"]: row for row in links.load_link_only()}
    bot = FetchBot()
    results: list[dict] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        for doc_id in dict.fromkeys(doc_ids):
            doc = await session.get(Document, doc_id)
            row = allowed.get(doc_id)
            if doc is None or row is None or row.get("url") != doc.url:
                raise ValueError(f"{doc_id} is not a seeded, manifest-matched link-only source")

            if doc.body and doc.origin == "fetched":
                results.append({"doc_id": doc_id, "outcome": "cached", "sha256": doc.content_hash})
                continue
            if doc.capture == "check-terms" and not terms_reviewed:
                result = {"doc_id": doc_id, "outcome": "terms_review_required"}
                await audit.record(session, None, audit.Channel.coverage_gaps, result)
                results.append(result)
                continue

            parsed = urlparse(doc.url)
            ruling = await bot.robots_for(client, parsed.hostname or "", parsed.scheme or "https")
            permitted, reason = ruling.allows(doc.url)
            await audit.record(
                session,
                None,
                audit.Channel.fetch_log,
                {
                    "doc_id": doc_id,
                    "stage": "robots_preflight",
                    "url": doc.url,
                    "permitted": permitted,
                    "reason": reason,
                    "robots_status": ruling.status,
                },
            )
            if not permitted:
                result = {"doc_id": doc_id, "outcome": "robots_disallowed", "reason": reason}
                await audit.record(session, None, audit.Channel.coverage_gaps, result)
                results.append(result)
                continue

            fetched = await bot.fetch(client, doc.url)
            result = {"doc_id": doc_id, **{k: v for k, v in asdict(fetched).items() if k != "text"}}
            await audit.record(session, None, audit.Channel.fetch_log, result)
            if not fetched.ok or not fetched.text:
                await audit.record(session, None, audit.Channel.coverage_gaps, result)
                results.append(result)
                continue

            now = dt.datetime.now(dt.UTC).isoformat()
            doc.body = f"SOURCE: {doc.url}\nRETRIEVED: {now}\n\n{fetched.text}"
            doc.retrieved_at = now
            doc.sha256 = fetched.sha256
            doc.content_hash = fetched.sha256
            doc.status = "ok"
            doc.origin = "fetched"
            typing = inventory.classify(
                url=doc.url,
                source_type=doc.source_type,
                jurisdictions=doc.jurisdictions,
                has_text=True,
            )
            doc.doc_type = typing.doc_type
            doc.tier = typing.tier
            doc.doc_type_reason = typing.reason
            await audit.record(
                session,
                None,
                audit.Channel.doc_types,
                {
                    "doc_id": doc_id,
                    "doc_type": typing.doc_type,
                    "tier": typing.tier,
                    "origin": "fetched",
                    "primary": typing.primary,
                    "reason": typing.reason,
                },
            )
            results.append({"doc_id": doc_id, "outcome": "ok", "sha256": fetched.sha256})
            await session.flush()
    return results
