"""A blocked source stays a gap; an allowed source retains provenance."""

from __future__ import annotations

import pytest

from app.db.models import Document
from app.modules.rule_extraction.pipeline import ingest
from app.modules.rule_extraction.pipeline.fetch import FetchResult, RobotsRuling


class _Session:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.added = []

    async def get(self, _type, doc_id):
        return self.doc if doc_id == self.doc.doc_id else None

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass


class _Client:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


@pytest.mark.asyncio
async def test_robots_block_does_not_create_source_text(monkeypatch) -> None:
    doc = Document(
        doc_id="D088",
        jurisdictions="Jersey City, NJ",
        url="https://example.org/ordinance.pdf",
        source_type="official",
        capture="link-only",
        origin="provided",
    )
    session = _Session(doc)

    class Bot:
        async def robots_for(self, *_args):
            return RobotsRuling("example.org", False, "disallowed", status=403)

        async def fetch(self, *_args):
            raise AssertionError("A blocked source must never be fetched")

    monkeypatch.setattr(
        ingest.links, "load_link_only", lambda: [{"doc_id": "D088", "url": doc.url}]
    )
    monkeypatch.setattr(ingest, "FetchBot", Bot)
    monkeypatch.setattr(ingest.httpx, "AsyncClient", _Client)
    result = await ingest.ingest_documents(session, ["D088"])
    assert result[0]["outcome"] == "robots_disallowed"
    assert doc.body is None
    assert doc.origin == "provided"


@pytest.mark.asyncio
async def test_capture_keeps_hash_and_retrieval_time(monkeypatch) -> None:
    doc = Document(
        doc_id="D089",
        jurisdictions="MA",
        url="https://example.org/bill.html",
        source_type="official",
        capture="link-only",
        origin="provided",
    )
    session = _Session(doc)

    class Bot:
        async def robots_for(self, *_args):
            return RobotsRuling("example.org", True, "allowed", status=200)

        async def fetch(self, *_args):
            return FetchResult(
                doc.url,
                "ok",
                status=200,
                text="Bill text " * 100,
                sha256="a" * 64,
                bytes=1000,
                content_type="text/html",
            )

    monkeypatch.setattr(
        ingest.links, "load_link_only", lambda: [{"doc_id": "D089", "url": doc.url}]
    )
    monkeypatch.setattr(ingest, "FetchBot", Bot)
    monkeypatch.setattr(ingest.httpx, "AsyncClient", _Client)
    result = await ingest.ingest_documents(session, ["D089"])
    assert result[0]["outcome"] == "ok"
    assert doc.origin == "fetched"
    assert doc.content_hash == "a" * 64
    assert doc.retrieved_at
    assert "Bill text" in doc.body
