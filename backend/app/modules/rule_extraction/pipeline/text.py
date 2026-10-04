"""Undo the hard line wrapping that PDF-to-text extraction leaves behind.

Why this exists: the model is asked to copy a supporting sentence verbatim,
and on a hard-wrapped document it copies exactly one *physical line* instead -
"shall not make any oral or written inquiry", seventy-seven characters that
start and end mid-clause. That span passes verification, because the verifier
normalises whitespace before comparing, but it does not carry the obligation
the record claims, which is the one thing the span is for.

So the body is unwrapped before it reaches the model. Verification still runs
against the stored original, and whitespace normalisation means a span quoted
from the unwrapped text still matches the wrapped source.

Only documents that really are hard-wrapped are touched. A document whose
paragraphs each sit on one long line is left exactly as it is.
"""

from __future__ import annotations

import re

# A wrapped document's lines pile up against the margin. These two thresholds
# separate that shape from paragraph-per-line text; both were measured across
# the 54 supplied documents, where the two populations are far apart (wrapped:
# p95 <= 131 and 0.22-0.69 of lines in the band; unwrapped: p95 up to 1336 and
# at most 0.09 in it).
_MAX_WRAP_WIDTH = 150
_MIN_BAND_SHARE = 0.20
# How close to the margin a line must reach to read as mechanically broken
# rather than ended on purpose.
_BAND = 12
# Below this there is not enough of a line-length distribution to judge.
_MIN_LINES = 20

# A line opening a new block is never a continuation of the one above, however
# full that line is: subsection markers, numbered and bulleted items, section
# headings.
_BLOCK_OPENER = r"""
        \(\s*[0-9a-zA-Z]{1,4}\s*\)      # (a) (12) (iv)
      | [0-9]+\s*[.)]                   # 1. or 1)
      | [a-zA-Z]\s*[.)]\s               # b. or A)
      | §                               # § 46:8-55
      | [-*•‣●·]\s  # bullets
      | (?:SECTION|Section|Sec\.|ARTICLE|Article|CHAPTER|Chapter|PART|Part)\b
"""
_NEW_BLOCK = re.compile(f"^(?:{_BLOCK_OPENER})", re.VERBOSE)
# A marker sitting alone on its line - "(2)", "b.", "3." - is a label for the
# text beneath it, so the text comes up to join it.
_BARE_MARKER = re.compile(f"^(?:{_BLOCK_OPENER})\\s*$", re.VERBOSE)

# A sentence that has actually finished. Checked on the line above a break to
# tell a deliberate break from one the extractor inserted.
_SENTENCE_END = re.compile(r"[.!?:;][\"'’”)\]]*$")
# ... unless the "." is really an abbreviation or an enumerator, which carries
# no information about whether the sentence continues.
_ABBREVIATION_END = re.compile(
    r"(?:\b[A-Za-z]\.|\b(?:No|Nos|Art|Sec|ch|Ch|cl|Cl|pt|Pt|para|Para|seq|et\sseq|"
    r"etc|eg|ie|cf|vs|v|Inc|Corp|Co|Ltd|Jr|Sr|Mr|Mrs|Ms|Dr|St|Ave|Rev|Stat|"
    r"Gen|Laws|Code|Ann|Supp|ed|Ed)\.)$"
)
# A break mid-sentence is usually followed by text that cannot begin one.
_CONTINUES_SENTENCE = re.compile(r"^[a-z0-9’'\"),;:—–-]")


def wrap_width(lines: list[str]) -> int | None:
    """The column this document wraps at, or None if it is not hard-wrapped."""
    lengths = sorted(len(line) for line in lines if line.strip())
    if len(lengths) < _MIN_LINES:
        return None
    # p95 rather than max, so a single overlong line cannot set the margin.
    p95 = lengths[int(len(lengths) * 0.95)]
    if p95 > _MAX_WRAP_WIDTH:
        return None
    band = sum(1 for n in lengths if n >= p95 - _BAND)
    if band < len(lengths) * _MIN_BAND_SHARE:
        return None
    return p95


def _ends_sentence(line: str) -> bool:
    stripped = line.rstrip()
    if not _SENTENCE_END.search(stripped):
        return False
    return not _ABBREVIATION_END.search(stripped)


def _is_continuation(previous: str, line: str, threshold: int) -> bool:
    """Does `line` carry on the text of `previous` rather than start afresh?

    Three ways it can, in order of how much they are trusted:

    1. `previous` reaches the right margin, so the extractor broke it there
       and not the author.
    2. `previous` is a bare subsection marker, which labels what follows.
    3. `previous` stops mid-sentence and `line` opens with something that
       cannot start one. This is the case the margin test alone misses: the
       extractor also emits short fragments ("A housing" / "provider" /
       "shall not make any oral or written inquiry") that sit nowhere near
       the margin yet plainly continue each other.
    """
    if not line.strip() or _NEW_BLOCK.match(line.lstrip()):
        return False
    if len(previous) >= threshold:
        return True
    if _BARE_MARKER.match(previous.strip()):
        return True
    return not _ends_sentence(previous) and bool(_CONTINUES_SENTENCE.match(line.lstrip()))


def unwrap(text: str) -> str:
    """Join lines broken by extraction, leaving deliberate breaks intact.

    Every decision reads the *original* physical line above the break, never
    the growing joined one - otherwise the first full line would swallow its
    whole paragraph regardless of where the sentences actually end.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    width = wrap_width(lines)
    if width is None:
        return text

    threshold = width - _BAND
    out: list[str] = []
    previous = ""

    for line in lines:
        if out and _is_continuation(previous, line, threshold):
            out[-1] = f"{out[-1]} {line.lstrip()}"
        else:
            out.append(line)
        previous = line

    return "\n".join(out)
