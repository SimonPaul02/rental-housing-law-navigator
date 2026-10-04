"""Translate cited prose into the compiled form, once per rule version.

Each coverage and exemption clause gets at most two readings.

The strict reading accepts a simple condition ("5 or more units") only when
the source states that whole sentence word for word. What it accepts is
source-verified: an answer resting only on such readings is high confidence.

Module A writes paraphrases, so the strict reading rarely fires. The second
reading (classify.py) is a fixed table of the shapes this corpus uses: a
condition on a fact the evaluator can test, a clause that names who or what
is regulated rather than which buildings, or nothing. Everything it settles
is marked as the machine's own reading - atoms carry `Basis.machine_read`,
set-aside clauses are kept in `classified` with their rationale, the rule is
`machine_classified` - so the answers it supports are low confidence and the
rule stays in the review queue.

What neither reading represents goes into `unmapped_text`, the rule is
`needs_review`, and the evaluator answers `unknown` for any address whose
result depends on it. Cross-references to another provision, contingencies,
and building conditions the source does not state verbatim always land
there: no path leads from "the source does not say this" to "applies" without
the answer saying so.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.modules.address_lookup.rule_adapter.classify import SCOPE, STRUCTURED_COVERAGE, classify
from app.modules.address_lookup.rule_adapter.models import (
    COMPILER_VERSION,
    Atom,
    Basis,
    ClassifiedClause,
    CompiledRule,
    CoverageBasis,
    EffectiveDate,
    Expr,
    Field,
    InvalidCompilation,
    Op,
    Origin,
    RelationType,
    ReviewState,
    SourceAnchor,
    UnmappedClause,
    ValuePeriod,
    rule_version_hash,
    validate_atom,
)

# --------------------------------------------------------------- splitting ---
# Exemption prose lists alternatives; coverage prose lists requirements. Both
# arrive as one string, so they are split before translation - an exemption
# list collapsed into a conjunction is the single easiest way to get a wrong
# answer out of this module.
# "or" is a disjunction in an exemption list and part of a comparison in
# "on or before" / "2 or fewer". Protecting those phrases is what keeps
# "certificate of occupancy issued on or before 1979-06-13" from being torn
# into two half-clauses, neither of which carries the date.
# Also protected: a hyphenated pair split across the conjunction, as in
# "low- or moderate-income households" - splitting there produces fragments
# ("Units deed-", "regulatory-restricted for low-") that mean nothing and
# cannot be reviewed.
# Comparatives are protected the same way ("six months or longer", "15 years
# or older"), as is "and/or". Separators inside parentheses never split:
# "(subsec. 9)" and "(as defined in Bus. & Prof. Code Section 16700)" are
# one clause's qualifier, not new clauses. Nor does a full stop after a legal
# abbreviation end a sentence.
_COMPARATIVE = (
    r"fewer|less|more|greater|above|before|after|on|longer|shorter|older|newer|"
    r"later|earlier|over|under|higher|lower"
)
_PROTECTED_OR = rf"(?<!\bon )(?<!- )(?<!/)\bor\b(?!\s+(?:{_COMPARATIVE})\b)"
_BARE_OR = re.compile(_PROTECTED_OR, re.I)
#: In an exemption list an "or" separates alternatives only before a capital
#: or a digit. Before a lower-case word it joins two halves of one phrase -
#: "shares a kitchen or bath", "a product or service", "a government agency or
#: court" - and splitting there leaves fragments that mean nothing alone.
_ALTERNATIVE_SEPARATOR = re.compile(rf";|\.\s+(?=[A-Z(])|{_PROTECTED_OR}(?=\s+[A-Z0-9])")
#: Coverage is a conjunction, so it is split only on separators that really do
#: join requirements. A leftover "or" inside a coverage clause is an ambiguity,
#: not a separator, and is sent to review rather than silently ANDed.
_REQUIREMENT_SEPARATOR = re.compile(r";|,\s*and\b|\.\s+(?=[A-Z(])", re.I)
_ABBREVIATION = re.compile(
    r"(?:\b(?:Bus|Prof|Civ|Gov|Govt|Code|Cal|Ann|Rev|Stat|Stats|subd|subdiv|subsec|sec|secs|"
    r"para|par|art|ch|cl|no|nos|pt|vol|approx|etc|inc|co|st|ave|vs|v|e\.g|i\.e)"
    r"|\b[A-Z](?:\.[A-Z])*)\.$",
    re.I,
)
#: In an exemption list, an "or" after one of these joins a negated list ("if
#: not owned by a trust, corporation, or LLC") and must not become a branch.
_NEGATION = re.compile(r"\b(?:not|unless|except|other than)\b", re.I)


def split_clauses(text: str, *, alternatives: bool = True) -> list[str]:
    separators = _ALTERNATIVE_SEPARATOR if alternatives else _REQUIREMENT_SEPARATOR
    depth, depths = 0, []
    for ch in text:
        depth += ch in "(["
        depth -= ch in ")]" and depth > 0
        depths.append(depth)

    parts: list[str] = []
    start = 0
    for m in separators.finditer(text):
        if depths[m.start()] or m.start() < start:
            continue
        piece = text[start : m.start()]
        token = m.group(0)
        if token.startswith(".") and _ABBREVIATION.search(text[: m.start() + 1]):
            continue
        if token.casefold() == "or" and _NEGATION.search(piece):
            continue
        parts.append(piece)
        start = m.end()
    parts.append(text[start:])
    return [part.strip(" .,;") for part in parts if part.strip(" .,;")]


def _anchor(
    span: str,
    record: Any,
    clause_id: str | None = None,
    origin: Origin | None = None,
    source_hash: str | None = None,
) -> SourceAnchor:
    return SourceAnchor(
        source_span=span.strip()[:400],
        source_doc_id=getattr(record, "source_doc_id", None),
        source_url=getattr(record, "source_url", None),
        clause_id=clause_id,
        origin=origin,
        source_hash=source_hash,
    )


def _is_unconditional(text: str) -> bool:
    stripped = text.strip().strip(".")
    return bool(
        re.fullmatch(
            r"(?:applies to all|all)\s+(?:residential\s+)?(?:rental\s+)?"
            r"(?:units?|properties|housing|dwellings?|tenancies)"
            r"(?:\s+(?:statewide|citywide))?|"
            r"(?:residential\s+)?rentals\s+(?:statewide|citywide)",
            stripped,
            re.I,
        )
    )


def content_hash(source_text: str | None) -> str | None:
    return (
        "sha256:" + hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else None
    )


def _source_contains(source_text: str | None, passage: str) -> bool:
    return bool(source_text and passage.strip() and passage in source_text)


def _source_standalone(source_text: str | None, passage: str) -> bool:
    """Machine verification cannot select a substring of a qualified sentence."""
    if not source_text or not passage.strip():
        return False
    want = passage.strip().strip(" .;")
    return any(
        part.strip().strip(" .;") == want for part in re.split(r"\.(?:\s|$)|\n\s*\n", source_text)
    )


def _source_context(source_text: str | None, passage: str) -> str | None:
    if not _source_contains(source_text, passage):
        return None
    return next(
        (
            part.strip()[:400]
            for part in re.split(r"\.(?:\s|$)|\n\s*\n", source_text or "")
            if passage in part
        ),
        None,
    )


_SIMPLE_PREFIX = (
    r"(?:(?:(?:this rule|the ordinance|it)\s+)?applies to\s+)?"
    r"(?:(?:buildings?|properties|premises|residential rentals?|rental units?)\s+"
    r"(?:with|having|containing)\s+)?"
)
_UNIT_PHRASE = re.compile(
    _SIMPLE_PREFIX + r"(?:(at least|more than|fewer than|less than|at most|no more than|up to)\s+)?"
    r"(\d+)\s*(or more|or greater|or fewer|or less)?\s*"
    r"(?:dwelling\s*|rental\s*)?units?",
    re.I,
)
_YEAR_PHRASE = re.compile(
    _SIMPLE_PREFIX + r"(?:buildings?\s+|properties\s+)?"
    r"(?:built|constructed|year built|construction)\s*"
    r"(?:on or before|before|prior to|no later than|on or after|after|since)\s*\d{4}",
    re.I,
)
_CERT_PHRASE = re.compile(
    _SIMPLE_PREFIX + r"(?:with\s+)?(?:a\s+)?certificate of occupancy\s*(?:issued\s+)?"
    r"(?:on or before|before|prior to|on or after|after)\s*\d{4}-\d{2}-\d{2}",
    re.I,
)


def verify_simple(passage: str) -> tuple[Field, Op, Any] | None:
    """Parse the *whole* source passage, never a convenient substring of it."""
    text = passage.strip().rstrip(".").strip()
    if re.search(
        r"\b(?:not|except|unless|either|subject to|other than)\b|"
        r"(?<!on )\bor\b(?!\s+(?:more|greater|fewer|less))",
        text,
        re.I,
    ):
        return None
    if m := _UNIT_PHRASE.fullmatch(text):
        before, raw, after = (m.group(1) or "").lower(), m.group(2), (m.group(3) or "").lower()
        if before in ("at least",) or after in ("or more", "or greater"):
            op = Op.gte
        elif before == "more than":
            op = Op.gt
        elif before in ("at most", "no more than", "up to") or after in ("or fewer", "or less"):
            op = Op.lte
        elif before in ("fewer than", "less than"):
            op = Op.lt
        elif not before and not after:
            op = Op.eq
        else:
            return None
        return Field.units, op, int(raw)
    if _YEAR_PHRASE.fullmatch(text):
        match = re.search(
            r"(on or before|before|prior to|no later than|on or after|after|since)\s*(\d{4})$",
            text,
            re.I,
        )
        assert match
        phrase, year = match.group(1).lower(), int(match.group(2))
        op = (
            Op.lte
            if phrase in ("on or before", "no later than")
            else Op.lt
            if phrase in ("before", "prior to")
            else Op.gte
            if phrase in ("on or after", "since")
            else Op.gt
        )
        return Field.year_built, op, year
    if _CERT_PHRASE.fullmatch(text):
        match = re.search(
            r"(on or before|before|prior to|on or after|after)\s*(\d{4}-\d{2}-\d{2})$", text, re.I
        )
        assert match
        try:
            date = dt.date.fromisoformat(match.group(2))
        except ValueError:
            return None
        phrase = match.group(1).lower()
        op = (
            Op.lte
            if phrase == "on or before"
            else Op.lt
            if phrase in ("before", "prior to")
            else Op.gte
            if phrase == "on or after"
            else Op.gt
        )
        return Field.certificate_of_occupancy_date, op, date
    return None


def compile_expression(
    text: str | dict | None,
    origin: Origin,
    record: Any,
    prefix: str,
    source_text: str | None = None,
) -> tuple[Expr, list[UnmappedClause], list[ClassifiedClause], list[Atom | Expr]]:
    """Compile coverage or exemption prose.

    Coverage is a conjunction of its clauses. Exemptions are a disjunction of
    clauses, each a conjunction of its own conditions.

    Each clause is read twice at most. First strictly: a simple condition the
    source states word for word becomes a source-verified atom. Failing that,
    the classifier's reading (classify.py) - an atom marked `machine_read`, a
    clause set aside as stating no building condition, or nothing, in which
    case the clause stays unmapped and its answer unknown.

    Returns (expression, unmapped, classified, moved): `moved` holds exemption
    alternatives that coverage prose stated as exclusions ("is exempt").
    """
    kind = "all" if origin is Origin.coverage else "any"
    if text is None or (isinstance(text, str) and not text.strip()):
        return Expr(kind, ()), [], [], []
    if isinstance(text, dict):
        text = "; ".join(f"{k}: {v}" for k, v in text.items())
    doc_id, source_hash = getattr(record, "source_doc_id", None), content_hash(source_text)

    def unmapped_clause(clause: str, reason: str, clause_id: str) -> UnmappedClause:
        return UnmappedClause(
            clause[:400],
            origin,
            reason,
            clause_id,
            source_span=_source_context(source_text, clause),
            source_doc_id=doc_id,
            source_hash=source_hash,
        )

    def classified_clause(clause: str, kind_: str, rationale: str, clause_id: str):
        return ClassifiedClause(
            clause[:400], origin, clause_id, kind_, rationale, _source_context(source_text, clause)
        )

    is_coverage = origin is Origin.coverage
    if _is_unconditional(text):
        if is_coverage and _source_standalone(source_text, text):
            return Expr(kind, ()), [], [], []
        return (
            Expr(kind, ()),
            [],
            [classified_clause(text, SCOPE, "states no condition on the building", f"{prefix}1")],
            [],
        )
    if is_coverage and STRUCTURED_COVERAGE.search(text):
        return (
            Expr(kind, ()),
            [
                unmapped_clause(
                    text,
                    "coverage lists covered and uncovered categories; its structure needs review",
                    f"{prefix}1",
                )
            ],
            [],
            [],
        )

    children: list[Atom | Expr] = []
    moved: list[Atom | Expr] = []
    unmapped: list[UnmappedClause] = []
    classified: list[ClassifiedClause] = []
    for n, clause in enumerate(split_clauses(text, alternatives=not is_coverage), start=1):
        clause_id = f"{prefix}{n}"
        if _is_unconditional(clause):
            classified.append(
                classified_clause(clause, SCOPE, "states no condition on the building", clause_id)
            )
            continue
        disjunctive = is_coverage and bool(_BARE_OR.search(clause))

        # The strict reading: a simple condition the source states verbatim.
        verified = (
            verify_simple(clause)
            if not disjunctive and _source_standalone(source_text, clause)
            else None
        )
        if verified is not None:
            field, op, value = verified
            atom = Atom(
                f"{clause_id}.1",
                field,
                op,
                value,
                _anchor(clause, record, clause_id, origin, source_hash),
            )
            children.append(atom)
            continue

        # The machine's own reading, low confidence by construction.
        reading = classify(clause, origin, record)
        if reading is None or reading.unresolved:
            unmapped.append(
                unmapped_clause(
                    clause,
                    reading.unresolved
                    if reading
                    else "coverage clause contains a disjunction; AND/OR structure needs review"
                    if disjunctive
                    else "no complete, independently verified condition in the source document",
                    clause_id,
                )
            )
            continue
        if not reading.atoms:
            classified.append(classified_clause(clause, reading.kind, reading.rationale, clause_id))
            continue
        # An "or" among owner facts ("a natural person or an LLC of natural
        # persons") cannot change an answer the data can reach: those facts are
        # never supplied, and only the unit bound beside them is ever definite.
        owner_only = all(field in _OWNER_FIELDS for field, _, _ in reading.atoms)
        if disjunctive and not reading.alternatives and not reading.as_exemption and not owner_only:
            unmapped.append(
                unmapped_clause(
                    clause,
                    "coverage clause contains a disjunction; AND/OR structure needs review",
                    clause_id,
                )
            )
            continue
        target = Origin.exemption if reading.as_exemption else origin
        atoms = tuple(
            Atom(
                f"{clause_id}.{i}",
                field,
                op,
                value,
                _anchor(clause, record, clause_id, target, source_hash),
                note=reading.rationale,
                basis=Basis.machine_read,
            )
            for i, (field, op, value) in enumerate(reading.atoms, start=1)
        )
        for atom in atoms:
            validate_atom(atom)
        piece: Atom | Expr = (
            atoms[0] if len(atoms) == 1 else Expr("any" if reading.alternatives else "all", atoms)
        )
        if reading.as_exemption:
            moved.append(piece)
        elif is_coverage and not reading.alternatives:
            children.extend(atoms)
        else:
            children.append(piece)

    return Expr(kind, tuple(children)), unmapped, classified, moved


_OWNER_FIELDS = {Field.owner_unit_count, Field.owner_is_natural_person, Field.owner_occupied}


# ------------------------------------------------------------------- dates ---
_DATE_IN_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTH_DATE = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}"
)
_SOURCE_DATE = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}|{_MONTH_DATE}", re.I)
_RATE_PERIOD = re.compile(
    rf"annual\s+rent\s+increases?.{{0,180}}?effective\s+({_MONTH_DATE}|\d{{4}}-\d{{2}}-\d{{2}})"
    rf"\s*,?\s*(?:through|to|until)\s+({_MONTH_DATE}|\d{{4}}-\d{{2}}-\d{{2}})",
    re.I | re.S,
)
_RATE_PERIOD_VALUE_FIRST = re.compile(
    r"(?:Allowable\s+Rent\s+Increase|Security\s+Deposit\s+Interest):\s*"
    rf"(\d+(?:\.\d+)?%)\s+for\s+({_MONTH_DATE}|\d{{4}}-\d{{2}}-\d{{2}})"
    rf"\s*[-–—]\s*({_MONTH_DATE}|\d{{4}}-\d{{2}}-\d{{2}})",
    re.I,
)
#: Words that, right before a date, make it the day a law starts to bind.
#: "effective until" is deliberately absent: it ends a version of the text.
_EFFECTIVE_CUE = re.compile(
    r"(?:\beffective|\btakes?\s+effect|\btook\s+effect|\bin\s+effect"
    r"|\b(?:go|goes|went|come|comes|came)\s+into\s+effect"
    r"|\bbec(?:ame|omes?)\s+(?:effective|operative)|\boperative"
    r"|\bnot\s+effective\s+until|\bbeginning|\bstarting)"
    r"(?:\s+(?:on|from|as\s+of))?\s*,?\s*$",
    re.I,
)

# An act that takes effect a stated interval after its enactment: the date is
# computed from two passages of the same source, never typed in by anyone.
_ORDINALS = {
    word: n
    for n, word in enumerate(
        "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split(),
        start=1,
    )
}
_ENACTED = re.compile(
    rf"\b(?:approved|enacted|signed)\s+(?:by\s+(?:the\s+)?governor\s+)?({_MONTH_DATE})",
    re.I,
)
_TAKES_EFFECT = r"this\s+act\s+shall\s+take\s+effect\s+"
_AFTER_ENACTMENT = r"\s+(?:next\s+)?following\s+(?:the\s+date\s+of\s+)?enactment"
_EFFECT_MONTH = re.compile(
    _TAKES_EFFECT + r"on\s+the\s+first\s+day\s+of\s+the\s+(\w+)\s+month" + _AFTER_ENACTMENT,
    re.I,
)
_EFFECT_DAY = re.compile(
    _TAKES_EFFECT + r"on\s+the\s+(\w+)\s+day" + _AFTER_ENACTMENT,
    re.I,
)
_EFFECT_NOW = re.compile(_TAKES_EFFECT + r"immediately", re.I)


@dataclass(frozen=True, slots=True)
class DerivedDate:
    """A law date computed from the act's own enactment clause."""

    date: dt.date
    enactment_span: str
    rule_span: str

    @property
    def note(self) -> str:
        return (
            f"effective {self.date.isoformat()}, computed from "
            f'"{" ".join(self.rule_span.split())}" and "{self.enactment_span}"'
        )


def _ordinal(word: str) -> int | None:
    word = word.casefold()
    if word in _ORDINALS:
        return _ORDINALS[word]
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", word)
    return int(m.group(1)) if m else None


def enactment_offset(source_text: str | None) -> DerivedDate | None:
    """The effective date an act states relative to its own enactment.

    Needs exactly one enactment date and exactly one effect clause in the
    same source; anything less definite returns None rather than a guess.
    "The first day of the twelfth month next following enactment", enacted
    July 20, 2026, is July 2026 plus twelve months, day one: 2027-07-01.
    """
    if not source_text:
        return None
    enacted = {
        (day, m.group(0))
        for m in _ENACTED.finditer(source_text)
        if (day := _source_date(" ".join(m.group(1).split())))
    }
    if len({day for day, _ in enacted}) != 1:
        return None
    day, enactment_span = sorted(enacted)[0]

    found: set[tuple[dt.date, str]] = set()
    for m in _EFFECT_MONTH.finditer(source_text):
        if (n := _ordinal(m.group(1))) is not None:
            months = day.month - 1 + n
            found.add((dt.date(day.year + months // 12, months % 12 + 1, 1), m.group(0)))
    for m in _EFFECT_DAY.finditer(source_text):
        if (n := _ordinal(m.group(1))) is not None:
            found.add((day + dt.timedelta(days=n), m.group(0)))
    for m in _EFFECT_NOW.finditer(source_text):
        found.add((day, m.group(0)))
    if len({d for d, _ in found}) != 1:
        return None
    date, rule_span = sorted(found)[0]
    return DerivedDate(date, enactment_span, rule_span)


def _source_date(raw: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        try:
            return dt.datetime.strptime(raw, "%B %d, %Y").date()
        except ValueError:
            return None


def _value_period(record: Any, source_text: str | None) -> ValuePeriod | None:
    if not source_text or not getattr(record, "key_value", None):
        return None
    for match in _RATE_PERIOD.finditer(source_text):
        start, end = _source_date(match.group(1)), _source_date(match.group(2))
        if start and end and start <= end:
            # The source passage also has to state the reported value nearby.
            following = source_text[match.end() : match.end() + 90]
            value = str(record.key_value).strip()
            percentage = re.search(r"\d+(?:\.\d+)?%", value)
            if value and (value in following or (percentage and percentage.group(0) in following)):
                return ValuePeriod(
                    start,
                    end,
                    _anchor(match.group(0), record, source_hash=content_hash(source_text)),
                )
    for match in _RATE_PERIOD_VALUE_FIRST.finditer(source_text):
        start, end = _source_date(match.group(2)), _source_date(match.group(3))
        value = str(record.key_value)
        if start and end and start <= end and match.group(1) in value:
            return ValuePeriod(
                start,
                end,
                _anchor(match.group(0), record, source_hash=content_hash(source_text)),
            )
    return None


def parse_effective_date(raw: str | None) -> EffectiveDate | None:
    """Keep the precision the source gave, as an interval.

    `2026` and `2026-07` are intervals, not points. Inside one, whether a rule
    is in force on a given day is genuinely unknown, and pretending otherwise
    is how a not-yet-effective rule gets reported as applying.
    """
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    parts = text.split("-")
    try:
        year = int(parts[0])
        if len(parts) >= 3:
            day = dt.date(year, int(parts[1]), int(parts[2]))
            return EffectiveDate(text, day, day, "day")
        if len(parts) == 2:
            month = int(parts[1])
            first = dt.date(year, month, 1)
            last = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
            return EffectiveDate(text, first, last, "month")
        return EffectiveDate(text, dt.date(year, 1, 1), dt.date(year, 12, 31), "year")
    except (ValueError, IndexError):
        return None


def candidate_dates(
    record: Any, source_text: str | None = None
) -> tuple[list[EffectiveDate], list[str], bool, ValuePeriod | None, list[EffectiveDate]]:
    """Source-backed law dates, the record dates the source does not back,
    and any annual value period - kept apart.

    Returns (found, notes, unresolved, period, unverified). `found` holds dates
    the source states with an effective cue, or computes from the act's own
    enactment clause. A record date the source does not back is not thrown
    away: it is returned in `unverified`, read later as an interval at low
    confidence, and flagged `unresolved` so the review queue still lists it.
    """
    found: list[EffectiveDate] = []
    unverified: list[EffectiveDate] = []
    notes: list[str] = []
    unresolved = False
    period = _value_period(record, source_text)
    derived = enactment_offset(source_text)
    rate_ranges = [
        m.span()
        for pattern in (_RATE_PERIOD, _RATE_PERIOD_VALUE_FIRST)
        for m in pattern.finditer(source_text or "")
    ]
    source_mentions: dict[dt.date, list[tuple[int, int, bool]]] = {}
    for match in _SOURCE_DATE.finditer(source_text or ""):
        day = _source_date(" ".join(match.group(0).split()))
        if day:
            in_rate = any(start <= match.start() < end for start, end in rate_ranges)
            source_mentions.setdefault(day, []).append((match.start(), match.end(), in_rate))

    def role(day: dt.date) -> str:
        if derived and day == derived.date:
            return "effective"
        mentions = source_mentions.get(day, [])
        for start, _, in_rate in mentions:
            if not in_rate and _EFFECTIVE_CUE.search(
                (source_text or "")[max(0, start - 55) : start]
            ):
                return "effective"
        if period and period.start == day and any(in_rate for _, _, in_rate in mentions):
            return "rate_period"
        if mentions and all(in_rate for _, _, in_rate in mentions):
            return "rate_period"
        return "ambiguous"

    def as_day(day: dt.date) -> EffectiveDate:
        return EffectiveDate(day.isoformat(), day, day, "day")

    primary = parse_effective_date(getattr(record, "effective_date", None))
    if primary:
        if not primary.is_exact:
            position = (source_text or "").find(primary.raw)
            if position >= 0 and _EFFECTIVE_CUE.search(
                (source_text or "")[max(0, position - 55) : position]
            ):
                found.append(primary)
            elif derived and primary.earliest <= derived.date <= primary.latest:
                found.append(as_day(derived.date))  # the source pins the day
            else:
                unresolved = True
                unverified.append(primary)
                notes.append(f"partial effective date {primary.raw} needs source review")
        else:
            meaning = role(primary.earliest)
            if meaning == "effective":
                found.append(primary)
            elif meaning == "rate_period" and period:
                notes.append(f"{primary.raw} starts a value period, not the rule")
            else:
                unresolved = True
                unverified.append(primary)
                notes.append(f"effective-date meaning of {primary.raw} needs source review")

    if derived:
        notes.append(derived.note)
        if all(f.earliest != derived.date for f in found):
            # The act's own clause gives a date the record does not: both stand
            # as candidates, and between them the answer is unknown.
            found.append(as_day(derived.date))

    # An additional candidate needs an explicit law-effective cue in the source.
    for attr in ("interaction", "requirement", "key_value"):
        text = getattr(record, attr, None)
        if not isinstance(text, str):
            continue
        for m in _DATE_IN_TEXT.finditer(text):
            parsed = parse_effective_date(m.group(0))
            if (
                parsed
                and role(parsed.earliest) == "effective"
                and all(parsed.earliest != f.earliest for f in found)
            ):
                found.append(parsed)
                notes.append(f"another source-backed effective date {m.group(0)} appears in {attr}")

    return found, notes, unresolved, period, unverified


# --------------------------------------------------------------- relations ---
# A state rule deferring to local law almost never says "yields to". It says
# what AB 1482 says: that it does not apply to property a local ordinance
# already covers.
_YIELDS = re.compile(
    r"yields? to|subordinate to|superseded by|preempted by|"
    r"does not apply (?:to|where)[^.;]{0,90}?\b(?:local|municipal|city)\b|"
    r"(?:housing|units?|property) (?:under|subject to) (?:a |any )?local\b|"
    r"except where .{0,40}(?:local|city|municipal)|"
    r"not subject to both",
    re.I,
)
_GOVERNS = re.compile(
    r"supersedes|preempts|overrides|takes precedence|governs instead|"
    r"local (?:ordinance|rent control) (?:applies|governs|controls)",
    re.I,
)
_BARS_LOCAL = re.compile(
    r"prohibits? (?:any )?local|bars? local|no (?:city|municipality) may", re.I
)
_BOTH = re.compile(r"in addition to|both apply|cumulative|does not limit|without limiting", re.I)


def _state_of(record: Any) -> str:
    """The state a rule belongs to, from either form of jurisdiction."""
    jurisdiction = (getattr(record, "jurisdiction", "") or "").strip()
    if getattr(record, "level", "") == "state":
        return jurisdiction.upper()
    return jurisdiction.rpartition(",")[2].strip().upper()


def compile_relations(
    record: Any, known_ids: set[str], peers: list[Any] | None = None
) -> tuple[list, list[UnmappedClause]]:
    """Turn `overrides` and `interaction` into directed, anchored relations.

    Module A's `overrides` list is empty throughout this corpus and interaction
    prose almost never names the other rule's id - it says "a local just cause
    ordinance" or "local rent control". So a counterpart is looked up instead:
    rules governing the *same issue* at the other level, in the same state.
    Every relation found this way is anchored to the span that evidences it and
    enters as `needs_review`, because the inference is about which rules the
    sentence means, and that is a reading a human should confirm.

    An unclear interaction becomes an unmapped clause, never an implicit
    "city wins": that default is a guess, and a state and a city rule can
    perfectly well both apply.
    """
    from app.modules.address_lookup.rule_adapter.models import Relation

    relations: list[Relation] = []
    unmapped: list[UnmappedClause] = []
    rule_id = getattr(record, "team_rule_id", "")
    issue = issue_key_for(record)
    level = getattr(record, "level", "")
    state = _state_of(record)

    for other in getattr(record, "overrides", None) or []:
        if other not in known_ids:
            unmapped.append(
                UnmappedClause(
                    text=f"overrides {other}",
                    origin=Origin.interaction,
                    reason="names a team_rule_id that does not exist in this run",
                )
            )
            continue
        relations.append(
            Relation(
                left_rule_id=rule_id,
                right_rule_id=other,
                issue_key=issue,
                relation=RelationType.yields_to,
                anchor=_anchor(f"overrides: {other}", record),
            )
        )

    text = getattr(record, "interaction", None) or ""
    if not text.strip():
        return relations, unmapped

    named = [rid for rid in known_ids if rid != rule_id and rid in text]
    kind: RelationType | None = None
    span = text
    if m := _BARS_LOCAL.search(text):
        kind, span = RelationType.bars_local, m.group(0)
    elif m := _YIELDS.search(text):
        kind, span = RelationType.yields_to, m.group(0)
    elif m := _GOVERNS.search(text):
        kind, span = RelationType.yields_to, m.group(0)
    elif m := _BOTH.search(text):
        kind, span = RelationType.both_apply, m.group(0)

    if kind is None:
        return relations, unmapped

    # Who the sentence is about. A state rule that defers to local law means the
    # city rules on the same issue in its state; a city rule that adds to state
    # law means the state rule on that issue.
    if named:
        counterparts = named
    elif peers is not None:
        wants_local = bool(re.search(r"local|municipal|city|ordinance", text, re.I))
        wants_state = bool(re.search(r"\bstate\b|federal", text, re.I))
        counterparts = [
            getattr(p, "team_rule_id", "")
            for p in peers
            if getattr(p, "team_rule_id", "") != rule_id
            and issue_key_for(p) == issue
            and _state_of(p) == state
            and (
                (level == "state" and wants_local and getattr(p, "level", "") == "city")
                or (level == "city" and wants_state and getattr(p, "level", "") == "state")
            )
        ]
    else:
        counterparts = []

    if not counterparts:
        unmapped.append(
            UnmappedClause(
                text=text[:400],
                origin=Origin.interaction,
                reason=(
                    "states an interaction but no rule in this run governs the same issue "
                    "on the other side of it"
                ),
            )
        )
        return relations, unmapped

    for other in counterparts:
        relations.append(
            Relation(
                left_rule_id=rule_id,
                right_rule_id=other,
                issue_key=issue,
                relation=kind,
                anchor=_anchor(span, record),
                condition=text[:300],
            )
        )

    return relations, unmapped


def issue_key_for(record: Any) -> str:
    """The obligation a rule governs.

    Category alone is too coarse to decide supersession - a rent cap and a
    notice period are both `rent_increase_limits` - so the key is the category
    narrowed by what the rule actually limits.
    """
    category = getattr(record, "category", "") or ""
    text = " ".join(
        str(getattr(record, f, "") or "") for f in ("title", "requirement", "key_value")
    ).lower()
    if category == "rent_increase_limits":
        if re.search(r"notice|days'? notice", text):
            return "rent_increase_notice"
        if re.search(r"frequenc|twice|number of increases|per (?:12|twelve)", text):
            return "rent_increase_frequency"
        return "rent_increase_cap"
    if category == "security_deposits":
        if re.search(r"interest", text):
            return "deposit_interest"
        if re.search(r"return|refund|itemi", text):
            return "deposit_return"
        return "deposit_cap"
    return category


# ------------------------------------------------------------------ public ---
def compile_rule(
    record: Any,
    *,
    known_rule_ids: set[str] | None = None,
    proposal=None,
    peers=None,
    source_text: str | None = None,
):
    """Compile one Module A record. Returns (CompiledRule, relations)."""
    known = known_rule_ids or set()
    coverage, cover_unmapped, cover_classified, moved = compile_expression(
        getattr(record, "coverage_conditions", None), Origin.coverage, record, "c", source_text
    )
    exemptions, exempt_unmapped, exempt_classified, _ = compile_expression(
        getattr(record, "exemptions", None), Origin.exemption, record, "x", source_text
    )
    exemptions = Expr("any", (*exemptions.children, *moved))
    relations, relation_unmapped = compile_relations(record, known, peers)
    # An interaction naming no rule that exists in this run has nothing to
    # displace or be displaced by, so it cannot change an answer: noted, not
    # left blocking the rule.
    classified = [*cover_classified, *exempt_classified]
    for clause in relation_unmapped:
        if clause.reason.startswith("states an interaction but no rule in this run"):
            classified.append(
                ClassifiedClause(
                    clause.text,
                    Origin.interaction,
                    "i1",
                    "interaction",
                    "names an interaction with no counterpart rule in this corpus",
                )
            )
    relation_unmapped = [
        c
        for c in relation_unmapped
        if not c.reason.startswith("states an interaction but no rule in this run")
    ]
    dates, date_notes, date_unresolved, value_period, unverified = candidate_dates(
        record, source_text
    )

    unmapped = (*cover_unmapped, *exempt_unmapped, *relation_unmapped)

    scope_notes: tuple[str, ...] = ()
    if proposal is not None:
        coverage, exemptions, unmapped, scope_notes = apply_proposal(
            record, proposal, coverage, exemptions, unmapped, source_text=source_text
        )

    notes = list(date_notes)
    notes.extend(
        f"AI proposed no building-level condition in {n}; awaiting review" for n in scope_notes
    )
    if len(dates) > 1:
        notes.append("more than one candidate effective date; between them the answer is unknown")

    raw_coverage = getattr(record, "coverage_conditions", None)
    unconditional = (
        isinstance(raw_coverage, str)
        and _is_unconditional(raw_coverage)
        and _source_standalone(source_text, raw_coverage)
    )
    covered_by_reading = any(c.origin is Origin.coverage for c in classified) and not any(
        u.origin is Origin.coverage for u in unmapped
    )
    basis = (
        CoverageBasis.explicit_unconditional
        if unconditional
        else CoverageBasis.conditions
        if not coverage.is_empty
        else CoverageBasis.classified
        if covered_by_reading
        else CoverageBasis.unresolved
    )
    machine_read = bool(classified) or any(
        a.basis is Basis.machine_read for a in (*coverage.atoms(), *exemptions.atoms())
    )
    compiled = CompiledRule(
        team_rule_id=getattr(record, "team_rule_id", ""),
        rule_version_hash=rule_version_hash(record),
        compiler_version=COMPILER_VERSION,
        review_state=ReviewState.needs_review
        if unmapped or basis is CoverageBasis.unresolved
        else ReviewState.machine_classified
        if machine_read
        else ReviewState.machine_verified,
        coverage_basis=basis,
        source_hash=content_hash(source_text),
        source_doc_id=getattr(record, "source_doc_id", None),
        level=getattr(record, "level", "state") or "state",
        jurisdiction=getattr(record, "jurisdiction", "") or "",
        status=getattr(record, "status", "in_force") or "in_force",
        effective_dates=tuple(dates),
        effective_date_unresolved=date_unresolved,
        unverified_dates=tuple(unverified),
        key_value_period=value_period,
        coverage=coverage,
        exemptions=exemptions,
        unmapped_text=unmapped,
        classified=tuple(classified),
        issue_key=issue_key_for(record),
        notes=tuple(notes),
    )
    return compiled, relations


def apply_proposal(
    record: Any,
    proposal: dict,
    coverage: Expr,
    exemptions: Expr,
    unmapped: tuple,
    *,
    source_text: str | None = None,
):
    """Fold a validated model proposal into a partial compilation.

    Every atom is re-validated against a matching passage in Document.body.
    A proposal is only a suggestion; an invented date, threshold, or field is
    dropped and the clause stays unmapped.
    """

    def anchored(atom_json: dict, clause: UnmappedClause) -> Atom | None:
        span = (atom_json.get("source_span") or "").strip()
        if not _source_contains(source_text, span):
            return None
        try:
            field = Field(atom_json["field"])
            op = Op(atom_json["op"])
        except (KeyError, ValueError):
            return None
        value = atom_json.get("value")
        if field in DATE_FIELDS_LOCAL and isinstance(value, str):
            parsed = parse_effective_date(value)
            value = parsed.earliest if parsed and parsed.is_exact else None
            if value is None:
                return None
        atom = Atom(
            id=str(atom_json.get("id") or "p"),
            field=field,
            op=op,
            value=value,
            anchor=_anchor(
                span, record, clause.clause_id, clause.origin, content_hash(source_text)
            ),
        )
        try:
            validate_atom(atom)
        except InvalidCompilation:
            return None
        return (
            atom
            if _normalise(span) == _normalise(clause.text)
            and _source_standalone(source_text, span)
            and verify_simple(span) == (field, op, value)
            else None
        )

    still_unmapped: list[UnmappedClause] = []
    resolved_as_scope: list[str] = []
    cover_children = list(coverage.children)
    exempt_children = list(exemptions.children)

    proposed = {c.get("clause_id") or c.get("text", ""): c for c in proposal.get("clauses", [])}
    for clause in unmapped:
        match = proposed.get(clause.clause_id) or proposed.get(clause.text)
        if match is None:
            still_unmapped.append(clause)
            continue

        verdict = match.get("verdict", "condition")
        span = (match.get("source_span") or "").strip()
        span_holds = _source_contains(source_text, span)
        annotated = {
            **match,
            "clause_id": clause.clause_id,
            "origin": str(clause.origin),
            "source_doc_id": getattr(record, "source_doc_id", None),
            "source_hash": content_hash(source_text),
            "atoms": [
                {
                    **item,
                    "clause_id": clause.clause_id,
                    "origin": str(clause.origin),
                    "source_doc_id": getattr(record, "source_doc_id", None),
                    "source_hash": content_hash(source_text),
                }
                for item in match.get("atoms", [])
                if isinstance(item, dict)
            ],
        }

        if verdict == "no_building_condition":
            # A source quote makes the suggestion inspectable; only a human
            # decision can clear a clause as adding no building condition.
            if span_holds:
                resolved_as_scope.append(f"{clause.origin}: {clause.text[:160]}")
            still_unmapped.append(
                UnmappedClause(
                    clause.text,
                    clause.origin,
                    "AI clearance requires human review",
                    clause.clause_id,
                    span if span_holds else None,
                    getattr(record, "source_doc_id", None),
                    content_hash(source_text),
                    annotated,
                )
            )
            continue

        atoms = [a for a in (anchored(j, clause) for j in match.get("atoms", [])) if a]
        if len(atoms) != 1 or len(match.get("atoms", [])) != 1:
            still_unmapped.append(
                UnmappedClause(
                    clause.text,
                    clause.origin,
                    "AI interpretation needs human review or complete simple verification",
                    clause.clause_id,
                    span if span_holds else None,
                    getattr(record, "source_doc_id", None),
                    content_hash(source_text),
                    annotated,
                )
            )
            continue
        if clause.origin is Origin.coverage:
            cover_children.extend(atoms)
        else:
            exempt_children.append(Expr("all", tuple(atoms)) if len(atoms) > 1 else atoms[0])

    return (
        Expr(coverage.kind, tuple(cover_children)),
        Expr(exemptions.kind, tuple(exempt_children)),
        tuple(still_unmapped),
        tuple(resolved_as_scope),
    )


DATE_FIELDS_LOCAL = {Field.certificate_of_occupancy_date}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
