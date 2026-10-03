"""Anthropic client for Module A extraction.

Model/effort policy: we call claude-opus-5 and deliberately pass neither
`thinking` nor `output_config.effort`. On Opus 5 the documented defaults are
adaptive thinking ON and effort `high`, which is what we want for careful
statutory reading, and `messages.parse()` populates `output_config.format`
itself - so we leave that object alone.
"""

from __future__ import annotations

import anthropic

from app.core.config import settings


class LLMNotConfiguredError(RuntimeError):
    """ANTHROPIC_API_KEY is absent, so extraction cannot run."""


_client: anthropic.AsyncAnthropic | None = None


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


def get_client() -> anthropic.AsyncAnthropic:
    """Lazily build a shared async client.

    The SDK also resolves ANTHROPIC_API_KEY / an `ant auth login` profile on
    its own, but we check explicitly so the API can return a clean 503 with a
    useful message instead of failing deep inside a request.
    """
    global _client
    if not is_configured():
        raise LLMNotConfiguredError(
            "ANTHROPIC_API_KEY is not set. Module A extraction is unavailable; "
            "everything else (corpus browsing, Module B, Module C) works without it."
        )
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
