"""Citation normalisation and verification.

A citation is only trustworthy if it is either stated in the document text or
derivable from the source URL. Anything else is a candidate hallucination and
is penalised rather than silently accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True, slots=True)
class CitationCheck:
    canonical: str
    raw: str
    found_in_text: bool
    section_in_text: bool
    derivable_from_url: bool
    note: str | None = None

    @property
    def verified(self) -> bool:
        return self.found_in_text or self.section_in_text or self.derivable_from_url


# Patterns are ordered: the first match wins, so put specific before general.
_NORMALISERS: list[tuple[re.Pattern[str], str]] = [
    # California codes
    (
        re.compile(r"\bcal(?:ifornia)?\.?\s*civ(?:il)?\.?\s*code\s*§?\s*([\d.]+)", re.I),
        r"Cal. Civ. Code § \1",
    ),
    (re.compile(r"\bcivil\s+code\s*§?\s*([\d.]+)", re.I), r"Cal. Civ. Code § \1"),
    (
        re.compile(r"\bcal(?:ifornia)?\.?\s*gov(?:ernment)?\.?\s*code\s*§?\s*([\d.]+)", re.I),
        r"Cal. Gov't Code § \1",
    ),
    (
        re.compile(
            r"\bbus(?:iness)?\.?\s*(?:&|and)\s*prof(?:essions)?\.?\s*code\s*§?\s*([\d.]+)", re.I
        ),
        r"Cal. Bus. & Prof. Code § \1",
    ),
    # New Jersey
    (re.compile(r"\bN\.?\s*J\.?\s*S\.?\s*A\.?\s*§?\s*([\d:\-.]+)", re.I), r"N.J.S.A. \1"),
    (re.compile(r"\bP\.?\s*L\.?\s*(\d{4}),?\s*c\.?\s*(\d+)", re.I), r"P.L.\1, c.\2"),
    # Massachusetts
    (
        re.compile(
            r"\b(?:M\.?G\.?L\.?|Mass(?:achusetts)?\.?\s*Gen(?:eral)?\.?\s*Laws?)\.?\s*"
            r"(?:c(?:h(?:apter)?)?\.?\s*)?([\d\w]+)[,\s]*§\s*([\d\w]+)",
            re.I,
        ),
        r"M.G.L. c. \1, § \2",
    ),
    (re.compile(r"\b(\d{3})\s*C\.?M\.?R\.?\s*([\d.]+)", re.I), r"\1 C.M.R. \2"),
    # Municipal codes
    (
        re.compile(r"\bB\.?M\.?C\.?\s*(?:ch(?:apter)?\.?\s*)?([\d.]+)", re.I),
        r"Berkeley Mun. Code ch. \1",
    ),
    (
        re.compile(r"\bS\.?F\.?\s*(?:Admin|Administrative)\.?\s*Code\s*§?\s*([\d.\w]+)", re.I),
        r"S.F. Admin. Code § \1",
    ),
    (re.compile(r"\bL\.?A\.?M\.?C\.?\s*§?\s*([\d.]+)", re.I), r"L.A. Mun. Code § \1"),
]

# Bills keep their own identity; they are not codified sections.
_BILL = re.compile(r"\b((?:AB|SB|A|S|H|HB)\s*\.?\s*\d{1,5})\b")

_SECTION_IN_URL = re.compile(r"(?:sectionNum|section|sec)=([\d.]+)", re.I)


def normalise(raw: str) -> str:
    """Map a citation to a canonical form. Unrecognised input is returned
    whitespace-normalised rather than mangled - better a verbatim citation
    than a wrong canonical one."""
    text = re.sub(r"\s+", " ", (raw or "")).strip().rstrip(".,;")
    if not text:
        return ""
    for pattern, replacement in _NORMALISERS:
        m = pattern.search(text)
        if m:
            canonical = pattern.sub(replacement, m.group(0))
            # Keep a trailing bill reference, e.g. "... § 16729 (AB 325)".
            bill = _BILL.search(text)
            if bill and bill.group(1).replace(" ", "") not in canonical.replace(" ", ""):
                canonical = f"{canonical} ({_tidy_bill(bill.group(1))})"
            return canonical
    if m := _BILL.search(text):
        return _tidy_bill(m.group(1))
    return text


def _tidy_bill(raw: str) -> str:
    return re.sub(r"\s*\.?\s*", "", raw).upper().replace("AB", "AB ").replace("SB", "SB ").strip()


def _loose(text: str) -> str:
    """Strip everything but alphanumerics for substring comparison, so
    'N.J.S.A. 46:8-21.2' matches 'NJSA 46:8-21.2'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _section_tokens(citation: str) -> list[str]:
    """Numeric tokens that identify the provision, e.g. 1947.12 -> '194712'."""
    return [_loose(t) for t in re.findall(r"\d+(?:[.:\-]\d+)*[A-Za-z]?", citation)]


def check(raw: str, *, document_text: str, source_url: str) -> CitationCheck:
    """Verify a citation against its document and URL.

    Three independent signals, weakest last:
      * the citation string itself appears (strongest, but brittle - a document
        saying "Civil Code 1947.12" will not contain "Cal. Civ. Code § 1947.12");
      * its section number appears, which is what a fabricated cite gets wrong;
      * the section number is in the source URL (leginfo ?sectionNum=1947.12).
    """
    canonical = normalise(raw)
    if not canonical:
        return CitationCheck("", raw, False, False, False, "empty citation")

    loose_doc = _loose(document_text)
    found = _loose(canonical) in loose_doc or _loose(raw) in loose_doc

    # A long token on its own is distinctive; short ones only count together,
    # so "c. 186, § 15B" needs both 186 and 15b present, not just one.
    tokens = [t for t in _section_tokens(canonical) if t]
    long_tokens = [t for t in tokens if len(t) >= 4]
    short_tokens = [t for t in tokens if len(t) < 4]
    section_in_text = bool(
        (long_tokens and any(t in loose_doc for t in long_tokens))
        or (len(short_tokens) >= 2 and all(t in loose_doc for t in short_tokens))
    )

    derivable = False
    if not found and not section_in_text and source_url:
        parsed = urlparse(source_url)
        haystack = _loose(f"{parsed.path} {parsed.query}")
        numbers = re.findall(r"\d+(?:[.:-]\d+)*", canonical)
        # A section number present in the URL is strong evidence, e.g.
        # leginfo ...?sectionNum=1947.12
        derivable = any(_loose(n) in haystack for n in numbers if len(n) >= 3)
        if not derivable:
            for value in parse_qs(parsed.query).values():
                if any(_loose(n) in _loose(" ".join(value)) for n in numbers if len(n) >= 3):
                    derivable = True
                    break

    note = None
    if not (found or section_in_text or derivable):
        note = "citation not found in source text and not derivable from the URL"
    return CitationCheck(canonical, raw, found, section_in_text, derivable, note)
