"""Link preflight: decide, per URL, whether we are permitted to fetch it.

Every link-only source is evaluated by the pipeline at run time. No host is
allow-listed or deny-listed in code: the ruling comes from that host's own
robots.txt on the day the run happens, and every decision is written to the
audit trail with the status and reason that produced it.

This is deliberately a separate stage from fetching. It is cheap, it touches
only robots.txt, and it produces the coverage picture - which documents we
may read and which are closed to us - before a single content page is
requested.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.rule_extraction.pipeline import audit
from app.modules.rule_extraction.pipeline.fetch import USER_AGENT, FetchBot


@dataclass(slots=True)
class LinkVerdict:
    doc_id: str
    url: str
    host: str
    permitted: bool
    reason: str
    robots_status: int | None
    crawl_delay: float | None


def load_link_only() -> list[dict[str, str]]:
    """Rows from corpus/links_only.csv. These are the only URLs the bot may
    ever touch - it does not discover or follow links."""
    path = settings.corpus_dir / "links_only.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


async def preflight(
    session: AsyncSession,
    run_id: str | None = None,
    *,
    urls: list[tuple[str, str]] | None = None,
) -> list[LinkVerdict]:
    """Check robots.txt for every link-only URL and record each ruling.

    `urls` is a list of (doc_id, url); omit it to use links_only.csv.
    """
    if urls is None:
        rows = load_link_only()
        urls = [
            (r.get("doc_id", "").strip(), r.get("url", "").strip())
            for r in rows
            if r.get("url", "").strip()
        ]

    bot = FetchBot()
    verdicts: list[LinkVerdict] = []

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        for doc_id, url in urls:
            host = (urlparse(url).hostname or "").lower()
            if not host:
                verdicts.append(LinkVerdict(doc_id, url, "", False, "unparseable URL", None, None))
                continue
            # One robots.txt per host per run; the bot caches the ruling.
            ruling = await bot.robots_for(client, host, urlparse(url).scheme or "https")
            permitted, why = ruling.allows(url)
            verdicts.append(
                LinkVerdict(
                    doc_id=doc_id,
                    url=url,
                    host=host,
                    permitted=permitted,
                    reason=why,
                    robots_status=ruling.status,
                    crawl_delay=ruling.crawl_delay,
                )
            )

    await audit.record_many(
        session,
        run_id,
        audit.Channel.fetch_log,
        [{"stage": "robots_preflight", **asdict(v)} for v in verdicts],
    )
    # Anything we may not read is a coverage gap, recorded as such.
    await audit.record_many(
        session,
        run_id,
        audit.Channel.coverage_gaps,
        [
            {"doc_id": v.doc_id, "url": v.url, "reason": f"robots: {v.reason}"}
            for v in verdicts
            if not v.permitted
        ],
    )
    return verdicts


def summarise(verdicts: list[LinkVerdict]) -> dict:
    by_host: dict[str, dict] = {}
    for v in verdicts:
        entry = by_host.setdefault(
            v.host,
            {
                "permitted": 0,
                "blocked": 0,
                "robots_status": v.robots_status,
                "crawl_delay": v.crawl_delay,
                "reason": v.reason,
            },
        )
        entry["permitted" if v.permitted else "blocked"] += 1
    return {
        "total": len(verdicts),
        "permitted": sum(1 for v in verdicts if v.permitted),
        "blocked": sum(1 for v in verdicts if not v.permitted),
        "by_reason": dict(Counter(v.reason for v in verdicts)),
        "by_host": by_host,
    }
