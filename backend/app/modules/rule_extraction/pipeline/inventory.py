"""Document typing and trust tiers.

The spec types every document with the model and then has a human confirm.
Most of the corpus can be typed deterministically from the manifest's
source_type plus the URL, which is cheaper, instant and reproducible; the
model is only worth spending on the genuinely ambiguous remainder.

Crucially, primary-vs-secondary comes from doc_type, not the manifest: the
manifest labels Justia and ecode360 copies "secondary", but they reproduce
the law text verbatim and are far more trustworthy than a law-firm blog.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

DocType = str  # law_text | bill | official_guide | code_mirror | secondary


@dataclass(frozen=True, slots=True)
class Typing:
    doc_type: DocType
    tier: str
    primary: bool
    reason: str
    needs_review: bool = False


# Trust per tier. 1b is a bill: its status and title are authoritative, its
# substantive content is not if all we have is a status page.
TIER_TRUST: dict[str, float] = {"1": 1.00, "1b": 1.00, "2": 0.85, "3": 0.80, "4": 0.60}
TIER_RANK: dict[str, int] = {"1": 0, "1b": 1, "2": 2, "3": 3, "4": 4}

_CODE_MIRROR_HOSTS = {
    "law.justia.com",
    "justia.com",
    "ecode360.com",
    "codelibrary.amlegal.com",
    "library.municode.com",
    "municode.com",
    "codepublishing.com",
    "amlegal.com",
    "sanjose.municipal.codes",
}

_BILL_HINTS = re.compile(
    r"leginfo\.legislature|malegislature\.gov/Bills|billTextClient|"
    r"/bills?/|legiscan|njleg\.state\.nj\.us",
    re.I,
)

_SECONDARY_HOSTS_HINT = re.compile(
    r"morganlewis|jdsupra|natlawreview|lexology|law360|reuters|nytimes|"
    r"apnews|bloomberg|globest|multifamily|nmhc|apartmentlist|"
    r"\.law\b|lawfirm|attorney|blog",
    re.I,
)

_GUIDE_PATH = re.compile(
    r"/faq|/guide|/guidance|/press|/news|/newsroom|/fact|/brochure|"
    r"/tenant|/rent-board|/rentboard|/resources",
    re.I,
)


def is_government(host: str) -> bool:
    host = host.lower()
    return (
        host.endswith(".gov") or ".gov." in host or host.endswith(".us") or host.endswith(".ca.gov")
    )


def classify(*, url: str, source_type: str | None, jurisdictions: str, has_text: bool) -> Typing:
    """Deterministic typing. `needs_review=True` means send it to the model."""
    host = (urlparse(url or "").hostname or "").lower()
    src = (source_type or "").lower()

    if host == "law.justia.com" and "/cases/" in (url or ""):
        return Typing("secondary", "4", False, "court-opinion mirror, not the court's source")

    if host in _CODE_MIRROR_HOSTS or any(host.endswith(h) for h in _CODE_MIRROR_HOSTS):
        return Typing(
            "code_mirror",
            "3",
            True,
            f"{host} republishes codified law verbatim",
        )

    if _BILL_HINTS.search(url or ""):
        return Typing("bill", "1b", True, "URL points at a bill or its status page")

    if is_government(host):
        if _GUIDE_PATH.search(url or ""):
            return Typing(
                "official_guide",
                "2",
                True,
                "government host, guidance/FAQ/press path",
            )
        # Government host serving an ordinance or statute document.
        if re.search(r"\.pdf$|ordinance|municipal|code|statute|chapter", url or "", re.I):
            return Typing("law_text", "1", True, "government host, law-text path")
        return Typing(
            "official_guide",
            "2",
            True,
            "government host, non-law path - verify it is not the statute itself",
            needs_review=True,
        )

    if "secondary" in src or _SECONDARY_HOSTS_HINT.search(url or ""):
        return Typing("secondary", "4", False, "non-government publisher: law firm, news or trade")

    if "official" in src:
        return Typing(
            "law_text",
            "1",
            True,
            "manifest marks it official but the host is not recognisably government",
            needs_review=True,
        )

    return Typing(
        "secondary",
        "4",
        False,
        "unrecognised host and source_type; defaulting to lowest trust",
        needs_review=True,
    )


def content_hash(body: str | None) -> str | None:
    if body is None:
        return None
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def tier_trust(tier: str | None) -> float:
    return TIER_TRUST.get(tier or "4", 0.60)


def better_tier(a: str | None, b: str | None) -> str | None:
    """Return the more trustworthy of two tiers."""
    if a is None:
        return b
    if b is None:
        return a
    return a if TIER_RANK.get(a, 9) <= TIER_RANK.get(b, 9) else b
