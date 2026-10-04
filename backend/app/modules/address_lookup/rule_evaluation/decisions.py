"""Decision types: what was checked, what it found, and why.

A decision here is not a single word. Geography, time, coverage and exemption
are reported separately, each with the fact it read and the rule condition it
read it against, because the interesting failures in this module are all of
the form "the right answer for the wrong reason". A single `unknown` cannot be
audited; `unknown because the certificate-of-occupancy date for A0132 is not
supplied and the San Francisco cutoff is 1979-06-13` can.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Ternary(StrEnum):
    """Three-valued truth. `unknown` is a first-class answer, not a failure."""

    true = "true"
    false = "false"
    unknown = "unknown"

    def __bool__(self) -> bool:  # pragma: no cover - guard against accident
        raise TypeError("Ternary must be compared explicitly, not treated as a bool")


def conjunction(values: list[Ternary]) -> Ternary:
    """`all`: false if any child is false, else unknown if any is unknown.

    A definite false outranks an unknown on purpose. That is what answers the
    challenge's small-landlord case: an exemption needing "owner occupied AND
    2 or fewer units" cannot apply to a 32-unit building whoever owns it, so
    the missing owner name never has to be resolved.
    """
    if any(v is Ternary.false for v in values):
        return Ternary.false
    if any(v is Ternary.unknown for v in values):
        return Ternary.unknown
    return Ternary.true


def disjunction(values: list[Ternary]) -> Ternary:
    """`any`: true if any child is true, else unknown if any is unknown."""
    if any(v is Ternary.true for v in values):
        return Ternary.true
    if any(v is Ternary.unknown for v in values):
        return Ternary.unknown
    return Ternary.false


class Reason(StrEnum):
    """Why a check landed where it did. Stable strings, safe to assert on."""

    # geography
    jurisdiction_match = "jurisdiction_match"
    jurisdiction_mismatch = "jurisdiction_mismatch"
    legal_city_unresolved = "legal_city_unresolved"
    # time
    in_force = "in_force"
    before_effective_date = "before_effective_date"
    inside_effective_interval = "inside_effective_interval"
    conflicting_effective_dates = "conflicting_effective_dates"
    effective_date_missing = "effective_date_missing"
    effective_date_unverified = "effective_date_unverified"
    status_pending = "status_pending"
    status_failed = "status_failed"
    # facts
    fact_satisfied = "fact_satisfied"
    fact_violated = "fact_violated"
    fact_not_supplied = "fact_not_supplied"
    fact_invalid = "fact_invalid"
    fact_conflicted = "fact_conflicted"
    fact_never_supplied = "fact_never_supplied"
    fact_wrong_scope = "fact_wrong_scope"
    certificate_date_not_supplied = "certificate_date_not_supplied"
    certificate_cutoff_year_ambiguous = "certificate_cutoff_year_ambiguous"
    year_built_proxy_used = "year_built_proxy_used"
    membership_proxy_used = "membership_proxy_used"
    # structure
    unconditional_reviewed = "unconditional_reviewed"
    coverage_classified = "coverage_classified"
    rule_clause_unmapped = "rule_clause_unmapped"
    rule_not_approved = "rule_not_approved"
    # interactions
    superseded_by = "superseded_by"
    governing_rule_unknown = "governing_rule_unknown"
    relation_qualification_unknown = "relation_qualification_unknown"
    both_apply = "both_apply"
    possible_conflict = "possible_conflict"


@dataclass(slots=True)
class CheckTrace:
    """One check: the condition, the fact, and the verdict."""

    check: str
    value: Ternary
    reason: Reason
    detail: str
    condition_id: str | None = None
    source_span: str | None = None
    field: str | None = None
    fact_value: Any = None
    fact_status: str | None = None
    #: A `Basis` value when the check rests on anything weaker than the source
    #: or a reviewer: a classified clause, a presumed fact, a proxy.
    basis: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "value": str(self.value),
            "reason": str(self.reason),
            "detail": self.detail,
            "condition_id": self.condition_id,
            "source_span": self.source_span,
            "field": self.field,
            "fact_value": (
                self.fact_value.isoformat()
                if isinstance(self.fact_value, dt.date)
                else self.fact_value
            ),
            "fact_status": self.fact_status,
            "basis": self.basis,
        }


class Confidence(StrEnum):
    """How much of an answer a reader can check against the source.

    `high`: every check behind it is the cited text or a named reviewer.
    `low`: at least one rests on the machine's own reading, and the reasons
    say which - so it is shown for human review rather than hidden.
    """

    high = "high"
    low = "low"


class BaseResult(StrEnum):
    """The internal vocabulary, richer than the submission's.

    `does_not_apply` earns its place here and is dropped on the way out: the UI
    and the audit trail both need to say "this rule was considered and
    definitely does not cover you", which the submission format has no word for.
    """

    applies = "applies"
    does_not_apply = "does_not_apply"
    unknown = "unknown"
    not_yet_effective = "not_yet_effective"
    pending = "pending"
    failed = "failed"
    superseded = "superseded"


SUBMISSION_RESULTS = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}


@dataclass(slots=True)
class BaseDecision:
    """One rule against one address on one date, before interactions."""

    team_rule_id: str
    address_id: str
    as_of: dt.date
    result: BaseResult
    issue_key: str
    geography: Ternary = Ternary.unknown
    time: Ternary = Ternary.unknown
    coverage: Ternary = Ternary.unknown
    exemption: Ternary = Ternary.false
    checks: list[CheckTrace] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    conflict_flag: bool = False
    conflict_reason: str | None = None

    @property
    def could_be_relevant(self) -> bool:
        """Worth reporting at all: not a definite miss."""
        return self.result is not BaseResult.does_not_apply and self.result is not BaseResult.failed

    def to_json(self) -> dict[str, Any]:
        return {
            "team_rule_id": self.team_rule_id,
            "address_id": self.address_id,
            "as_of": self.as_of.isoformat(),
            "result": str(self.result),
            "issue_key": self.issue_key,
            "geography": str(self.geography),
            "time": str(self.time),
            "coverage": str(self.coverage),
            "exemption": str(self.exemption),
            "unresolved_fields": list(self.unresolved_fields),
            "conflict_flag": self.conflict_flag,
            "conflict_reason": self.conflict_reason,
            "checks": [c.to_json() for c in self.checks],
        }


@dataclass(slots=True)
class Decision:
    """A base decision after the interaction pass, plus its explanation."""

    base: BaseDecision
    result: BaseResult
    explanation: str = ""
    conflict_flag: bool = False
    conflict_reason: str | None = None
    superseded_by: str | None = None
    interaction_checks: list[CheckTrace] = field(default_factory=list)
    confidence: Confidence = Confidence.high
    confidence_reasons: list[str] = field(default_factory=list)

    @property
    def team_rule_id(self) -> str:
        return self.base.team_rule_id

    @property
    def address_id(self) -> str:
        return self.base.address_id

    def to_json(self) -> dict[str, Any]:
        return {
            **self.base.to_json(),
            "final_result": str(self.result),
            "explanation": self.explanation,
            "conflict_flag": self.conflict_flag,
            "conflict_reason": self.conflict_reason,
            "superseded_by": self.superseded_by,
            "interaction_checks": [c.to_json() for c in self.interaction_checks],
            "confidence": str(self.confidence),
            "confidence_reasons": list(self.confidence_reasons),
        }
