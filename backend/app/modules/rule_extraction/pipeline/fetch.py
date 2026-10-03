"""Fetch bot for link-only sources.

Deliberately conservative, because the brief forbids scraping in violation of
a site's terms:

  * only URLs already named in corpus/links_only.csv - never discovered,
    never followed, no crawling;
  * robots.txt is consulted per host and obeyed, including Crawl-delay, with
    a 5 second floor regardless of what robots.txt permits;
  * a block is a result, not an obstacle. A 403, a login wall or a captcha
    marks the document unavailable - we never retry it differently, rotate a
    user agent, or otherwise work around it;
  * one request per URL per run, cached by URL, so a rerun never touches the
    network again;
  * every request is logged with status, size and hash.

Fetched publisher text is treated as quotable evidence, not redistributable
content: it stays out of git until the organizers say otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "RentalHousingLawNavigator/0.1 (MIT AI Hackathon research prototype; "
    "contact: mail@simon-paul.com)"
)
CRAWL_DELAY_FLOOR = 5.0
MIN_USEFUL_CHARS = 500
REQUEST_TIMEOUT = 30.0

# A page that is really a consent/login/anti-bot interstitial, not content.
_WALL = re.compile(
    r"enable javascript to continue|checking your browser|"
    r"verify you are human|are you a robot|please complete the security check|"
    r"sign in to continue|subscribe to (?:read|continue)|"
    r"access denied|request blocked|cloudflare",
    re.I,
)


@dataclass(slots=True)
class RobotsRuling:
    """What robots.txt said about one host, and why.

    Derived per host at run time. Nothing about any specific site is encoded
    in this file - the ruling comes from the response.
    """

    host: str
    allowed_default: bool
    reason: str
    status: int | None = None
    crawl_delay: float | None = None
    parser: RobotFileParser | None = None

    def allows(self, url: str) -> tuple[bool, str]:
        if self.parser is not None:
            ok = self.parser.can_fetch(USER_AGENT, url)
            return ok, ("robots.txt allows this path" if ok else "robots.txt disallows this path")
        return self.allowed_default, self.reason


@dataclass(slots=True)
class FetchResult:
    url: str
    outcome: str  # ok | robots_disallowed | http_error | unusable | error
    status: int | None = None
    text: str | None = None
    sha256: str | None = None
    bytes: int = 0
    content_type: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def html_to_text(html: str) -> str:
    """Strip markup without a parser dependency. Good enough for statute
    pages, which are overwhelmingly text."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&sect;", "§"),
        ("&rsquo;", "’"),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def pdf_to_text(data: bytes) -> str:
    """pypdf rather than a pdftotext binary, so the container needs no extra
    system package."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        log.warning("pypdf not installed; cannot read PDF")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:  # noqa: BLE001 - a broken PDF is a result
        log.warning("PDF parse failed: %s", exc)
        return ""


class FetchBot:
    """One per run. Holds robots rulings and per-host pacing."""

    def __init__(self, *, crawl_delay_floor: float = CRAWL_DELAY_FLOOR) -> None:
        self._robots: dict[str, RobotsRuling] = {}
        self._delays: dict[str, float] = {}
        self._last_request: dict[str, float] = {}
        self._floor = crawl_delay_floor
        self._lock = asyncio.Lock()

    async def robots_for(
        self, client: httpx.AsyncClient, host: str, scheme: str = "https"
    ) -> RobotsRuling:
        """Resolve one host's robots.txt into a ruling, cached per run.

        Status handling matters more than it looks. A 403 on robots.txt is not
        "no rules published" - it is the site refusing automated access, and
        fetching its pages anyway would be working around a block, which the
        brief forbids. Only an explicit 404/410 means there are no rules.
        """
        if host in self._robots:
            return self._robots[host]

        url = f"{scheme}://{host}/robots.txt"
        ruling: RobotsRuling
        try:
            response = await client.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            status = response.status_code
            body = response.text or ""

            if status == 200 and _WALL.search(body[:3000]):
                ruling = RobotsRuling(
                    host,
                    False,
                    "robots.txt answered with an anti-bot interstitial; "
                    "the host is refusing automated access",
                    status=status,
                )
            elif status == 200:
                parser = RobotFileParser()
                parser.parse(body.splitlines())
                delay = parser.crawl_delay(USER_AGENT)
                if delay:
                    self._delays[host] = float(delay)
                ruling = RobotsRuling(
                    host,
                    True,
                    "robots.txt fetched and parsed",
                    status=status,
                    crawl_delay=delay and float(delay),
                    parser=parser,
                )
            elif status in (401, 403):
                ruling = RobotsRuling(
                    host,
                    False,
                    f"HTTP {status} on robots.txt - the host refuses automated "
                    "access, so its pages are off limits too",
                    status=status,
                )
            elif status in (404, 410):
                ruling = RobotsRuling(
                    host,
                    True,
                    f"HTTP {status} - no robots.txt published",
                    status=status,
                )
            elif status == 429:
                ruling = RobotsRuling(
                    host, False, "HTTP 429 on robots.txt - rate limited", status=status
                )
            elif status >= 500:
                ruling = RobotsRuling(
                    host,
                    False,
                    f"HTTP {status} on robots.txt - cannot confirm permission, "
                    "so treated as disallowed for this run",
                    status=status,
                )
            else:
                ruling = RobotsRuling(
                    host,
                    True,
                    f"HTTP {status} on robots.txt - treated as absent",
                    status=status,
                )
        except httpx.HTTPError as exc:
            ruling = RobotsRuling(
                host,
                False,
                f"robots.txt unreachable ({type(exc).__name__}) - permission "
                "cannot be confirmed, so disallowed for this run",
            )

        self._robots[host] = ruling
        return ruling

    async def _wait_turn(self, host: str) -> None:
        """Serialise per host and honour the delay. Held across the sleep so
        two coroutines cannot both decide it is their turn."""
        async with self._lock:
            delay = max(self._delays.get(host, 0.0), self._floor)
            last = self._last_request.get(host)
            if last is not None:
                remaining = delay - (time.monotonic() - last)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request[host] = time.monotonic()

    async def fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        expect: list[str] | None = None,
    ) -> FetchResult:
        """Fetch one URL. `expect` are strings one of which should appear in a
        genuine page - a cite or keyword - used to catch soft 404s."""
        parsed = urlparse(url)
        host, scheme = parsed.hostname or "", parsed.scheme or "https"
        if not host:
            return FetchResult(url, "error", reason="unparseable URL")

        ruling = await self.robots_for(client, host, scheme)
        permitted, why = ruling.allows(url)
        if not permitted:
            return FetchResult(url, "robots_disallowed", status=ruling.status, reason=why)

        await self._wait_turn(host)

        try:
            response = await client.get(
                url,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*"},
            )
        except httpx.HTTPError as exc:
            return FetchResult(url, "error", reason=f"{type(exc).__name__}: {exc}"[:300])

        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        raw = response.content

        if response.status_code != 200:
            # 403/401 mean we are not welcome. That is the final answer.
            return FetchResult(
                url,
                "http_error",
                status=response.status_code,
                bytes=len(raw),
                content_type=content_type,
                reason=f"HTTP {response.status_code}; not retried or worked around",
            )

        if "pdf" in content_type or url.lower().endswith(".pdf"):
            text = pdf_to_text(raw)
        else:
            text = html_to_text(response.text)

        if len(text) < MIN_USEFUL_CHARS:
            return FetchResult(
                url,
                "unusable",
                status=200,
                bytes=len(raw),
                content_type=content_type,
                reason=f"only {len(text)} chars of text extracted",
            )
        if _WALL.search(text[:3000]):
            return FetchResult(
                url,
                "unusable",
                status=200,
                bytes=len(raw),
                content_type=content_type,
                reason="looks like a login, consent or anti-bot interstitial",
            )
        if expect and not any(token.lower() in text.lower() for token in expect):
            return FetchResult(
                url,
                "unusable",
                status=200,
                bytes=len(raw),
                content_type=content_type,
                reason=f"none of the expected tokens present: {expect}",
            )

        return FetchResult(
            url,
            "ok",
            status=200,
            text=text,
            sha256=_hash(text),
            bytes=len(raw),
            content_type=content_type,
        )
