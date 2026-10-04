"""The compiled form of a rule: a small, checkable expression language.

Module A's output is cited prose. Prose cannot be executed against five
hundred buildings, so it is translated once per rule version into an
expression tree, and *that* is what the evaluator runs. Two properties matter
more than expressiveness:

  * **Every atom is anchored.** It carries the span of source text it came
    from, so a reviewer can read the clause and the translation side by side,
    and so an invented threshold has nowhere to hide.
  * **Anything untranslated is recorded, not dropped.** `unmapped_text` is the
    list of clauses the compiler could not represent faithfully. The evaluator
    treats them as unknown. The old runtime parser returned an empty predicate
    list for prose it did not recognise, and an empty list read as "no
    condition to test", which read as `applies` - so unrecognised legal text
    silently became unconditional coverage. An empty coverage expression means
    unconditional coverage only with a verified unconditional source statement
    or a version-bound human review.

The tree is JSON-serialisable by construction: no Python expressions, no
eval, nothing that could execute arbitrary code from a model proposal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

COMPILER_VERSION = "3"


class Field(StrEnum):
    """Every fact an atom is allowed to test.

    Closed on purpose: a proposal naming anything else is rejected rather than
    guessed at, because a field we cannot evaluate would silently become a
    pass. `owner_*` and `seasonal_rental` are listed because the law tests
    them, not because the data has them - they evaluate to unknown, which is
    the correct answer for this corpus and the reason the challenge asks for
    "unknown" on owner-type exceptions.
    """

    legal_city = "legal_city"
    legal_state = "legal_state"
    units = "units"
    year_built = "year_built"
    certificate_of_occupancy_date = "certificate_of_occupancy_date"
    use_code = "use_code"
    owner_occupied = "owner_occupied"
    owner_is_natural_person = "owner_is_natural_person"
    owner_unit_count = "owner_unit_count"
    seasonal_rental = "seasonal_rental"
    building_is_subsidised = "building_is_subsidised"
    los_angeles_rso_membership = "los_angeles_rso_membership"
    san_francisco_rent_ordinance_membership = "san_francisco_rent_ordinance_membership"


class Op(StrEnum):
    eq = "eq"
    ne = "ne"
    lt = "lt"
    lte = "lte"
    gt = "gt"
    gte = "gte"
    is_true = "is_true"
    is_false = "is_false"


#: Which operators make sense for which field, by value type.
NUMERIC_FIELDS = {Field.units, Field.year_built, Field.owner_unit_count}
DATE_FIELDS = {Field.certificate_of_occupancy_date}
TEXT_FIELDS = {Field.legal_city, Field.legal_state, Field.use_code}
BOOLEAN_FIELDS = {
    Field.owner_occupied,
    Field.owner_is_natural_person,
    Field.seasonal_rental,
    Field.building_is_subsidised,
    Field.los_angeles_rso_membership,
    Field.san_francisco_rent_ordinance_membership,
}
#: Facts this corpus cannot supply for any address. Kept explicit so an
#: explanation can say *why* something is unknown rather than only that it is.
NEVER_SUPPLIED = {
    Field.owner_occupied,
    Field.owner_is_natural_person,
    Field.owner_unit_count,
    Field.seasonal_rental,
    Field.building_is_subsidised,
    Field.los_angeles_rso_membership,
    Field.san_francisco_rent_ordinance_membership,
}

_ORDERED = {Op.lt, Op.lte, Op.gt, Op.gte}
_BOOLEAN_OPS = {Op.is_true, Op.is_false}


class Origin(StrEnum):
    """Which part of the rule record an expression was translated from."""

    coverage = "coverage"
    exemption = "exemption"
    interaction = "interaction"


class ReviewState(StrEnum):
    machine_verified = "machine_verified"
    human_approved = "human_approved"
    approved = "approved"  # legacy relation state; never grants coverage approval
    needs_review = "needs_review"
    rejected = "rejected"


class CoverageBasis(StrEnum):
    explicit_unconditional = "explicit_unconditional"
    conditions = "conditions"
    unresolved = "unresolved"


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """Where an atom's authority comes from."""

    source_span: str
    source_doc_id: str | None = None
    source_url: str | None = None
    clause_id: str | None = None
    origin: Origin | None = None
    source_hash: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "source_span": self.source_span,
            "source_doc_id": self.source_doc_id,
            "source_url": self.source_url,
            "clause_id": self.clause_id,
            "origin": str(self.origin) if self.origin else None,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True, slots=True)
class Atom:
    """One typed comparison against one fact."""

    id: str
    field: Field
    op: Op
    value: Any = None
    anchor: SourceAnchor | None = None
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "field": str(self.field), "op": str(self.op)}
        if self.value is not None:
            out["value"] = self.value.isoformat() if isinstance(self.value, dt.date) else self.value
        if self.anchor:
            out.update(self.anchor.to_json())
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True, slots=True)
class Expr:
    """`all` of clauses, or `any` of clauses. Atoms are leaves.

    An exemption list is an `any` of `all`s: the clauses are alternatives and
    the conditions inside one clause must hold together. "Owner occupied with
    at most two units, or a seasonal rental" is
    `any(all(owner_occupied, units <= 2), seasonal_rental)` and must never be
    flattened into one conjunction - flattening would let a missing owner name
    defeat the seasonal branch as well.
    """

    kind: Literal["all", "any"]
    children: tuple[Atom | Expr, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {self.kind: [child.to_json() for child in self.children]}

    def atoms(self):
        for child in self.children:
            if isinstance(child, Atom):
                yield child
            else:
                yield from child.atoms()

    @property
    def is_empty(self) -> bool:
        return not self.children


EMPTY_ALL = Expr("all", ())
EMPTY_ANY = Expr("any", ())


@dataclass(frozen=True, slots=True)
class UnmappedClause:
    """Legal text the compiler could not translate faithfully."""

    text: str
    origin: Origin
    reason: str
    clause_id: str = ""
    source_span: str | None = None
    source_doc_id: str | None = None
    source_hash: str | None = None
    proposal: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "origin": str(self.origin),
            "reason": self.reason,
            "clause_id": self.clause_id,
            "source_span": self.source_span,
            "source_doc_id": self.source_doc_id,
            "source_hash": self.source_hash,
            "proposal": self.proposal,
        }


class RelationType(StrEnum):
    yields_to = "yields_to"
    both_apply = "both_apply"
    bars_local = "bars_local"
    possible_conflict = "possible_conflict"


class Qualification(StrEnum):
    confirmed = "confirmed"
    excluded = "excluded"
    unresolved = "unresolved"


@dataclass(frozen=True, slots=True)
class Relation:
    """A reviewed, directed statement that one rule governs an issue another
    rule also speaks to.

    `issue_key` is deliberately narrower than a category: two rent rules can
    govern different obligations - a cap and a notice period - and only the
    rule that governs *this* obligation can supersede another on it.
    """

    left_rule_id: str
    right_rule_id: str
    issue_key: str
    relation: RelationType
    anchor: SourceAnchor | None = None
    condition: str | None = None
    review_state: ReviewState = ReviewState.needs_review
    qualification: Qualification = Qualification.unresolved
    valid_from: dt.date | None = None
    left_version_hash: str | None = None
    right_version_hash: str | None = None
    left_source_hash: str | None = None
    right_source_hash: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out = {
            "left_rule_id": self.left_rule_id,
            "right_rule_id": self.right_rule_id,
            "issue_key": self.issue_key,
            "relation": str(self.relation),
            "review_state": str(self.review_state),
            "condition": self.condition,
            "qualification": str(self.qualification),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "left_version_hash": self.left_version_hash,
            "right_version_hash": self.right_version_hash,
            "left_source_hash": self.left_source_hash,
            "right_source_hash": self.right_source_hash,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
        }
        if self.anchor:
            out.update(self.anchor.to_json())
        return out


@dataclass(frozen=True, slots=True)
class EffectiveDate:
    """An effective date at whatever precision the source gave.

    A day is a point; a month or a year is an interval, and inside that
    interval the answer is genuinely unknown. Treating `2026-07` as the first
    of July invents a fact and can turn a future rule into an applied one.
    """

    raw: str
    earliest: dt.date
    latest: dt.date
    precision: Literal["day", "month", "year"]

    @property
    def is_exact(self) -> bool:
        return self.precision == "day"

    def to_json(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "earliest": self.earliest.isoformat(),
            "latest": self.latest.isoformat(),
            "precision": self.precision,
        }


@dataclass(frozen=True, slots=True)
class ValuePeriod:
    """Dates during which a quoted numerical value is current, not law dates."""

    start: dt.date
    end: dt.date
    anchor: SourceAnchor

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "anchor": self.anchor.to_json(),
        }


@dataclass(slots=True)
class CompiledRule:
    """One rule version, translated and reviewable."""

    team_rule_id: str
    rule_version_hash: str
    compiler_version: str = COMPILER_VERSION
    review_state: ReviewState = ReviewState.needs_review
    coverage_basis: CoverageBasis = CoverageBasis.unresolved
    source_hash: str | None = None
    source_doc_id: str | None = None
    # Geography and time are compiled into their own fields rather than into
    # the coverage tree, so the evaluator never tests the same jurisdiction
    # twice and can report geography separately from coverage.
    level: str = "state"
    jurisdiction: str = ""
    status: str = "in_force"
    effective_dates: tuple[EffectiveDate, ...] = ()
    effective_date_unresolved: bool = False
    key_value_period: ValuePeriod | None = None
    coverage: Expr = EMPTY_ALL
    exemptions: Expr = EMPTY_ANY
    unmapped_text: tuple[UnmappedClause, ...] = ()
    issue_key: str = ""
    notes: tuple[str, ...] = ()

    @property
    def has_unmapped(self) -> bool:
        return bool(self.unmapped_text)

    @property
    def is_usable(self) -> bool:
        """Approved, and with nothing untranslated that could change a result."""
        return (
            self.review_state in (ReviewState.machine_verified, ReviewState.human_approved)
            and not self.unmapped_text
            and self.coverage_basis is not CoverageBasis.unresolved
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "team_rule_id": self.team_rule_id,
            "rule_version_hash": self.rule_version_hash,
            "compiler_version": self.compiler_version,
            "review_state": str(self.review_state),
            "coverage_basis": str(self.coverage_basis),
            "source_hash": self.source_hash,
            "source_doc_id": self.source_doc_id,
            "level": self.level,
            "jurisdiction": self.jurisdiction,
            "status": self.status,
            "issue_key": self.issue_key,
            "effective_dates": [d.to_json() for d in self.effective_dates],
            "effective_date_unresolved": self.effective_date_unresolved,
            "key_value_period": self.key_value_period.to_json() if self.key_value_period else None,
            "coverage": self.coverage.to_json(),
            "exemptions": self.exemptions.to_json(),
            "unmapped_text": [u.to_json() for u in self.unmapped_text],
            "notes": list(self.notes),
        }


#: The fields whose change invalidates a compilation. A stable `team_rule_id`
#: is not enough on its own: Module A can correct the wording of a rule and
#: keep the id, and the old translation would then describe text that no
#: longer exists.
HASHED_FIELDS = (
    "title",
    "requirement",
    "source_doc_id",
    "source_url",
    "conflict_flag",
    "conflict_note",
    "jurisdiction",
    "level",
    "status",
    "effective_date",
    "coverage_conditions",
    "exemptions",
    "interaction",
    "overrides",
    "citation",
    "quoted_span",
    "category",
    "key_value",
)


def rule_version_hash(record: Any) -> str:
    """Hash the decision-bearing fields of a Module A record."""
    payload = {}
    for name in HASHED_FIELDS:
        value = getattr(record, name, None) if not isinstance(record, dict) else record.get(name)
        if isinstance(value, dt.date):
            value = value.isoformat()
        payload[name] = value
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# -------------------------------------------------------------- validation ---
class InvalidCompilation(ValueError):
    """A proposed translation that must not be trusted."""


def validate_atom(atom: Atom, *, allow_missing_anchor: bool = False) -> None:
    """Reject anything the evaluator could not run, or could run wrongly."""
    if atom.field not in set(Field):
        raise InvalidCompilation(f"unknown field {atom.field!r}")
    if atom.op not in set(Op):
        raise InvalidCompilation(f"unknown operator {atom.op!r}")

    is_boolean = atom.field in BOOLEAN_FIELDS
    if is_boolean and atom.op not in _BOOLEAN_OPS:
        raise InvalidCompilation(f"{atom.field} takes is_true/is_false, not {atom.op}")
    if not is_boolean and atom.op in _BOOLEAN_OPS:
        raise InvalidCompilation(f"{atom.op} is only valid on a boolean field, not {atom.field}")

    if atom.op in _BOOLEAN_OPS:
        if atom.value is not None:
            raise InvalidCompilation(f"{atom.op} takes no value")
    else:
        if atom.value is None:
            raise InvalidCompilation(f"{atom.field} {atom.op} needs a value")
        if atom.field in NUMERIC_FIELDS and not isinstance(atom.value, int):
            raise InvalidCompilation(f"{atom.field} needs an integer, got {atom.value!r}")
        if atom.field in DATE_FIELDS and not isinstance(atom.value, dt.date):
            raise InvalidCompilation(f"{atom.field} needs a date, got {atom.value!r}")
        if atom.field in TEXT_FIELDS and not isinstance(atom.value, str):
            raise InvalidCompilation(f"{atom.field} needs a string, got {atom.value!r}")

    if atom.field in TEXT_FIELDS and atom.op in _ORDERED:
        raise InvalidCompilation(f"{atom.field} cannot be ordered with {atom.op}")

    if not allow_missing_anchor and (atom.anchor is None or not atom.anchor.source_span.strip()):
        raise InvalidCompilation(f"atom {atom.id} has no supporting source span")


def validate_expr(expr: Expr, **kwargs) -> None:
    if expr.kind not in ("all", "any"):
        raise InvalidCompilation(f"unknown expression kind {expr.kind!r}")
    for child in expr.children:
        if isinstance(child, Atom):
            validate_atom(child, **kwargs)
        else:
            validate_expr(child, **kwargs)


def validate_compiled(rule: CompiledRule, **kwargs) -> None:
    validate_expr(rule.coverage, **kwargs)
    validate_expr(rule.exemptions, **kwargs)
    seen: set[str] = set()
    for atom in (*rule.coverage.atoms(), *rule.exemptions.atoms()):
        if atom.id in seen:
            raise InvalidCompilation(f"duplicate condition id {atom.id}")
        seen.add(atom.id)
