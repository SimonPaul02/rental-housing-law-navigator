"""Translate cited prose into the compiled form, once per rule version.

The compiler is deterministic first and asks a model only for what is left.
That ordering is not only about cost: a regex that matches "2 or fewer units"
is replayable and reviewable, and most of this corpus' coverage text is that
shape. What remains - nested exceptions, undefined terms, conditions that
depend on a fact nobody supplied - is where a proposal earns its keep, and
exactly where a reviewer should be looking.

The rule that makes the whole thing safe: text the compiler cannot represent
goes into `unmapped_text`, and a rule with unmapped text is `needs_review`.
The evaluator answers `unknown` for any address whose result depends on it. No
path exists from "I did not understand this clause" to "the rule applies".
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app.modules.address_lookup.rule_adapter.models import (
    COMPILER_VERSION,
    Atom,
    CompiledRule,
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
_PROTECTED_OR = r"(?<!on )(?<!- )\bor\b(?!\s+(?:fewer|less|more|greater|above|before|after|on))"
_ALTERNATIVE_SPLIT = re.compile(rf"\s*(?:;|{_PROTECTED_OR}|\.\s+)\s*", re.I)
#: Coverage is a conjunction, so it is split only on separators that really do
#: join requirements. A leftover "or" inside a coverage clause is an ambiguity,
#: not a separator, and is sent to review rather than silently ANDed.
_REQUIREMENT_SPLIT = re.compile(r"\s*(?:;|,\s*and\b|\.\s+)\s*", re.I)
_BARE_OR = re.compile(_PROTECTED_OR, re.I)


def split_clauses(text: str, *, alternatives: bool = True) -> list[str]:
    pattern = _ALTERNATIVE_SPLIT if alternatives else _REQUIREMENT_SPLIT
    return [part.strip(" .,;") for part in pattern.split(text) if part.strip(" .,;")]


# ---------------------------------------------------------------- patterns ---
# Each pattern returns one atom, or None when it does not apply. A pattern
# that matches but cannot produce a typed value must return None so the text
# lands in `unmapped_text` rather than becoming a half-translated condition.
_CO_DATE = re.compile(
    r"certificate of occupancy[^.;]*?(?:issued\s+)?(?:on or before|before|prior to)\s*"
    r"(\d{4})-(\d{2})-(\d{2})",
    re.I,
)
_CO_AFTER = re.compile(
    r"certificate of occupancy[^.;]*?(?:issued\s+)?(?:on or after|after)\s*(\d{4})-(\d{2})-(\d{2})",
    re.I,
)
_CO_ANY = re.compile(r"certificate of occupancy", re.I)
_BUILT_BEFORE = re.compile(
    r"(?:built|constructed|year built|construction)[^.;]{0,40}?"
    r"(?:on or before|before|prior to|no later than)\s*(\d{4})",
    re.I,
)
_BUILT_AFTER = re.compile(
    r"(?:built|constructed|year built|construction)[^.;]{0,40}?"
    r"(?:on or after|after|since)\s*(\d{4})",
    re.I,
)
_UNITS_AT_LEAST = re.compile(
    r"(\d+)\s*(?:\+|or more|or greater|or above)\s*(?:or more\s*)?(?:dwelling\s*|rental\s*)?units?",
    re.I,
)
_UNITS_FEWER_EQ = re.compile(
    r"(\d+)\s*(?:or fewer|or less)\s*(?:dwelling\s*|rental\s*)?units?", re.I
)
_UNITS_FEWER = re.compile(
    r"(?:fewer than|less than|under|no more than|up to|at most|maximum of)\s*(\d+)\s*"
    r"(?:dwelling\s*|rental\s*)?units?",
    re.I,
)
_UNITS_EXACT = re.compile(r"(?:exactly\s*)?(\d+)\s*(?:dwelling\s*|rental\s*)?units?\b", re.I)

_OWNER_OCCUPIED = re.compile(r"owner[- ]occupied|owner occupancy|resident owner", re.I)
_OWNER_NATURAL = re.compile(r"natural person|individual owner|not a (?:corporation|reit)", re.I)
_OWNER_PORTFOLIO = re.compile(
    r"owner of (?:no more than|fewer than|not more than)\s*(\d+)|"
    r"landlord(?:s)? (?:who )?own(?:ing|s)? (?:no more than|fewer than)\s*(\d+)",
    re.I,
)
_SEASONAL = re.compile(r"seasonal (?:rental|use|occupancy)|vacation rental|transient", re.I)
_SUBSIDISED = re.compile(
    r"subsidi[sz]ed|section 8|public housing|affordable housing (?:program|covenant)|"
    r"government[- ]assisted|lihtc",
    re.I,
)

# Prose that states no building-level condition at all. Recognising these
# keeps them out of `unmapped_text`: "residential rentals statewide" is not an
# untranslated condition, it is the absence of one, and a reviewer confirming
# that is different from a reviewer confirming we parsed a hard clause.
_UNCONDITIONAL = re.compile(
    r"^(?:all\s+)?(?:residential\s+)?(?:rental\s+)?(?:units?|properties|property|dwellings?|"
    r"housing|tenanc(?:y|ies)|buildings?|premises)?\s*"
    r"(?:statewide|citywide|in the (?:state|city)|throughout the (?:state|city))?\s*$",
    re.I,
)
_UNCONDITIONAL_PHRASES = re.compile(
    r"^(?:none|n/?a|not applicable|no (?:coverage )?conditions?|"
    r"applies to all (?:residential )?(?:rental )?(?:units?|properties|housing|dwellings?)|"
    r"all (?:residential )?(?:rental )?(?:units?|properties|housing|dwellings?)"
    r"(?: in (?:the )?[\w\s,.]+)?|"
    r"(?:residential )?(?:rental|tenanc\w+)s? (?:statewide|citywide)|statewide|citywide)\s*\.?$",
    re.I,
)


def _anchor(span: str, record: Any) -> SourceAnchor:
    return SourceAnchor(
        source_span=span.strip()[:400],
        source_doc_id=getattr(record, "source_doc_id", None),
        source_url=getattr(record, "source_url", None),
    )


def _atom(cid: str, f: Field, op: Op, value: Any, span: str, record: Any, note=None) -> Atom:
    atom = Atom(id=cid, field=f, op=op, value=value, anchor=_anchor(span, record), note=note)
    validate_atom(atom)
    return atom


def translate_clause(clause: str, cid: str, record: Any) -> list[Atom] | None:
    """Atoms for one clause, or None when nothing in it is representable.

    A clause can legitimately yield several atoms: "owner occupied with two or
    fewer units" is two conditions that must hold together.
    """
    atoms: list[Atom] = []
    index = 0

    def nid() -> str:
        nonlocal index
        index += 1
        return f"{cid}.{index}"

    if m := _CO_DATE.search(clause):
        atoms.append(
            _atom(
                nid(),
                Field.certificate_of_occupancy_date,
                Op.lte,
                dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                m.group(0),
                record,
            )
        )
    elif m := _CO_AFTER.search(clause):
        atoms.append(
            _atom(
                nid(),
                Field.certificate_of_occupancy_date,
                Op.gt,
                dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                m.group(0),
                record,
            )
        )

    if m := _BUILT_BEFORE.search(clause):
        atoms.append(_atom(nid(), Field.year_built, Op.lte, int(m.group(1)), m.group(0), record))
    elif m := _BUILT_AFTER.search(clause):
        atoms.append(_atom(nid(), Field.year_built, Op.gt, int(m.group(1)), m.group(0), record))

    if m := _UNITS_AT_LEAST.search(clause):
        atoms.append(_atom(nid(), Field.units, Op.gte, int(m.group(1)), m.group(0), record))
    elif m := _UNITS_FEWER_EQ.search(clause):
        atoms.append(_atom(nid(), Field.units, Op.lte, int(m.group(1)), m.group(0), record))
    elif m := _UNITS_FEWER.search(clause):
        atoms.append(_atom(nid(), Field.units, Op.lt, int(m.group(1)), m.group(0), record))

    if _OWNER_OCCUPIED.search(clause):
        atoms.append(_atom(nid(), Field.owner_occupied, Op.is_true, None, clause, record))
    if _OWNER_NATURAL.search(clause):
        atoms.append(_atom(nid(), Field.owner_is_natural_person, Op.is_true, None, clause, record))
    if m := _OWNER_PORTFOLIO.search(clause):
        limit = int(m.group(1) or m.group(2))
        atoms.append(_atom(nid(), Field.owner_unit_count, Op.lte, limit, m.group(0), record))
    if _SEASONAL.search(clause):
        atoms.append(_atom(nid(), Field.seasonal_rental, Op.is_true, None, clause, record))
    if _SUBSIDISED.search(clause):
        atoms.append(_atom(nid(), Field.building_is_subsidised, Op.is_true, None, clause, record))

    if atoms:
        return atoms

    # A certificate-of-occupancy cutoff we could not pin to a day is still a
    # real condition on a fact we do not hold: record it as an atom with no
    # value so the answer is unknown for the right reason.
    if _CO_ANY.search(clause):
        return [
            Atom(
                id=f"{cid}.1",
                field=Field.certificate_of_occupancy_date,
                op=Op.lte,
                value=dt.date(1, 1, 1),
                anchor=_anchor(clause, record),
                note="cutoff date not stated to the day in the source",
            )
        ]
    return None


def _is_unconditional(text: str) -> bool:
    stripped = text.strip().strip(".")
    return bool(_UNCONDITIONAL_PHRASES.match(stripped) or _UNCONDITIONAL.match(stripped))


def compile_expression(
    text: str | dict | None, origin: Origin, record: Any, prefix: str
) -> tuple[Expr, list[UnmappedClause]]:
    """Compile coverage or exemption prose.

    Coverage is a conjunction of its clauses. Exemptions are a disjunction of
    clauses, each a conjunction of its own conditions.
    """
    kind = "all" if origin is Origin.coverage else "any"
    if text is None or (isinstance(text, str) and not text.strip()):
        return Expr(kind, ()), []
    if isinstance(text, dict):
        text = "; ".join(f"{k}: {v}" for k, v in text.items())
    if _is_unconditional(text):
        return Expr(kind, ()), []

    children: list[Atom | Expr] = []
    unmapped: list[UnmappedClause] = []

    is_coverage = origin is Origin.coverage
    for n, clause in enumerate(split_clauses(text, alternatives=not is_coverage), start=1):
        if _is_unconditional(clause):
            continue
        # A coverage clause that still contains a disjunction cannot be made a
        # conjunct without changing its meaning, so it goes to review.
        if is_coverage and _BARE_OR.search(clause):
            unmapped.append(
                UnmappedClause(
                    text=clause[:400],
                    origin=origin,
                    reason="coverage clause contains a disjunction; AND/OR structure needs review",
                )
            )
            continue
        atoms = translate_clause(clause, f"{prefix}{n}", record)
        if atoms is None:
            unmapped.append(
                UnmappedClause(
                    text=clause[:400],
                    origin=origin,
                    reason="no deterministic pattern matched this clause",
                )
            )
            continue
        if origin is Origin.coverage:
            children.extend(atoms)
        else:
            # One exemption clause is an AND of its own conditions, and the
            # clauses are alternatives.
            children.append(Expr("all", tuple(atoms)) if len(atoms) > 1 else atoms[0])

    return Expr(kind, tuple(children)), unmapped


# ------------------------------------------------------------------- dates ---
_DATE_IN_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


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


def candidate_dates(record: Any) -> tuple[list[EffectiveDate], list[str]]:
    """Every effective date the record offers, plus notes about disagreement.

    More than one candidate is not an error to be averaged away: before all of
    them the rule is future, after all of them it is effective, and between
    them the answer is unknown and the conflict is reported.
    """
    found: list[EffectiveDate] = []
    notes: list[str] = []

    primary = parse_effective_date(getattr(record, "effective_date", None))
    if primary:
        found.append(primary)

    # An interaction or requirement sometimes states a second date in prose.
    for attr in ("interaction", "requirement", "key_value"):
        text = getattr(record, attr, None)
        if not isinstance(text, str):
            continue
        for m in _DATE_IN_TEXT.finditer(text):
            parsed = parse_effective_date(m.group(0))
            if parsed and all(parsed.earliest != f.earliest for f in found):
                found.append(parsed)
                notes.append(f"a second date {m.group(0)} appears in {attr}")

    return found, notes


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
def compile_rule(record: Any, *, known_rule_ids: set[str] | None = None, proposal=None, peers=None):
    """Compile one Module A record. Returns (CompiledRule, relations)."""
    known = known_rule_ids or set()
    coverage, cover_unmapped = compile_expression(
        getattr(record, "coverage_conditions", None), Origin.coverage, record, "c"
    )
    exemptions, exempt_unmapped = compile_expression(
        getattr(record, "exemptions", None), Origin.exemption, record, "x"
    )
    relations, relation_unmapped = compile_relations(record, known, peers)
    dates, date_notes = candidate_dates(record)

    unmapped = (*cover_unmapped, *exempt_unmapped, *relation_unmapped)

    scope_notes: tuple[str, ...] = ()
    if proposal is not None:
        coverage, exemptions, unmapped, scope_notes = apply_proposal(
            record, proposal, coverage, exemptions, unmapped
        )

    notes = list(date_notes)
    notes.extend(f"no building-level condition in {n}" for n in scope_notes)
    if len(dates) > 1:
        notes.append("more than one candidate effective date; between them the answer is unknown")

    compiled = CompiledRule(
        team_rule_id=getattr(record, "team_rule_id", ""),
        rule_version_hash=rule_version_hash(record),
        compiler_version=COMPILER_VERSION,
        review_state=ReviewState.needs_review if unmapped else ReviewState.approved,
        level=getattr(record, "level", "state") or "state",
        jurisdiction=getattr(record, "jurisdiction", "") or "",
        status=getattr(record, "status", "in_force") or "in_force",
        effective_dates=tuple(dates),
        coverage=coverage,
        exemptions=exemptions,
        unmapped_text=unmapped,
        issue_key=issue_key_for(record),
        notes=tuple(notes),
    )
    return compiled, relations


def apply_proposal(record: Any, proposal: dict, coverage: Expr, exemptions: Expr, unmapped: tuple):
    """Fold a validated model proposal into a partial compilation.

    Every atom is re-validated here and must quote text that genuinely occurs
    in the rule record. A proposal is a suggestion about wording we already
    hold - never a source of new facts - so an invented date, threshold or
    field is dropped and the clause stays unmapped.
    """
    haystack = " ".join(
        str(getattr(record, f, "") or "")
        for f in ("coverage_conditions", "exemptions", "interaction", "quoted_span", "requirement")
    ).lower()

    def anchored(atom_json: dict) -> Atom | None:
        span = (atom_json.get("source_span") or "").strip()
        if not span or _normalise(span) not in _normalise(haystack):
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
            anchor=_anchor(span, record),
        )
        try:
            validate_atom(atom)
        except InvalidCompilation:
            return None
        return atom

    still_unmapped: list[UnmappedClause] = []
    resolved_as_scope: list[str] = []
    cover_children = list(coverage.children)
    exempt_children = list(exemptions.children)

    proposed = {c.get("text", ""): c for c in proposal.get("clauses", [])}
    for clause in unmapped:
        match = proposed.get(clause.text)
        if match is None:
            still_unmapped.append(clause)
            continue

        verdict = match.get("verdict", "condition")
        span = (match.get("source_span") or "").strip()
        span_holds = bool(span) and _normalise(span) in _normalise(haystack)

        if verdict == "no_building_condition":
            # The clause describes who is bound, restates the jurisdiction, or
            # cross-references another ordinance, rather than narrowing which
            # buildings are covered. Accepted only with a span that really
            # occurs, and recorded in the rule's notes so a reviewer can
            # overturn it: this is the one verdict that can turn an unknown
            # into an applies, so it leaves a trail.
            if span_holds:
                resolved_as_scope.append(f"{clause.origin}: {clause.text[:160]}")
            else:
                still_unmapped.append(clause)
            continue

        atoms = [a for a in (anchored(j) for j in match.get("atoms", [])) if a]
        if not atoms:
            still_unmapped.append(clause)
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
