"""Unchanged source text reuses the reviewed model response."""

from __future__ import annotations

import pytest

from app.db.models import Document, ExtractionCache
from app.modules.rule_extraction import service


class _Session:
    def __init__(self) -> None:
        self.cached = ExtractionCache(
            cache_key="x" * 64,
            doc_id="D089",
            content_hash="y" * 64,
            prompt_version="test",
            model="test",
            response={"rules": [], "document_note": "No in-scope obligations."},
            input_tokens=0,
            output_tokens=0,
        )

    async def get(self, cls, _key):
        return self.cached if cls is ExtractionCache else None

    async def execute(self, _statement):
        return None

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_cached_extraction_does_not_call_model(monkeypatch) -> None:
    doc = Document(
        doc_id="D089",
        jurisdictions="MA",
        url="https://example.org/bill",
        body="SOURCE: https://example.org/bill\n\nBill information without an in-scope rule.",
    )

    def model_not_called():
        raise AssertionError("The model should not be called on a cache hit")

    monkeypatch.setattr(service, "get_client", model_not_called)
    result = await service.extract_document(_Session(), doc)
    assert result.rules == []
    assert result.document_note == "No in-scope obligations."
    assert result.input_tokens == 0
