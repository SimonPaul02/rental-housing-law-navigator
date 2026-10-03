"""Deterministic coverage evaluation for Module B.

`coverage_conditions` arrives as prose written by Module A. We parse it into
predicates and evaluate each against the address record. The challenge is
explicit that "unknown" is a valid - and preferred - answer when coverage
turns on a fact the data does not carry, so every predicate reports one of
APPLIES / FAILS / UNKNOWN rather than guessing.

Three facts the sample data genuinely cannot supply:
  * owner identity (no owner names), so small-landlord and owner-occupancy
    exceptions can never be resolved;
  * certificate-of-occupancy dates (we only have year built), so a building
    in the cutoff year is unknown by construction;
  * unit counts and years built are missing for whole cities (Berkeley, San
    Diego, most of New Jersey, Boston apartment rows).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import StrEnum


class Outcome(StrEnum):
    applies = "applies"
    fails = "does_not_apply"
    unknown = "unknown"


@dataclass(slots=True)
class AddressFacts:
    address_id: str
    legal_city: str | None
    legal_state: str | None
    year_built: int | None
    units: int | None
    use_code: str | None = None


@dataclass(slots=True)
class Predicate:
    kind: str
    outcome_if_missing: str
    describe: str
    # Set when the predicate needs a field the data lacks entirely.
    requires: str | None = None
    year: int | None = None
    co_date: dt.date | None = None
    count: int | None = None
    direction: str | None = None  # "on_or_before" | "after" | "at_least" | "fewer_than"


@dataclass(slots=True)
class CoverageVerdict:
    outcome: Outcome
    reasons: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


# --------------------------------------------------------------- parsing ---
_CO_DATE = re.compile(
    r"certificate of occupancy[^.]*?(?:on or before|before|issued on or before)\s*"
    r"(\d{4})-(\d{2})-(\d{2})",
    re.I,
)
_CO_LOOSE = re.compile(r"certificate of occupancy", re.I)
_BUILT_ON_BEFORE = re.compile(
    r"(?:built|constructed|year built)[^.]{0,40}?(?:on or before|before|prior to)\s*(\d{4})",
    re.I,
)
_BUILT_AFTER = re.compile(
    r"(?:built|constructed|year built)[^.]{0,40}?(?:after|on or after|since)\s*(\d{4})",
    re.I,
)
_UNITS_AT_LEAST = re.compile(r"(\d+)\s*(?:\+|or more|or greater)\s*(?:dwelling\s*)?units?", re.I)
_UNITS_FEWER = re.compile(
    r"(?:fewer than|less than|under|up to|no more than|2 or fewer|or fewer)\s*(\d+)?\s*"
    r"(?:dwelling\s*)?units?",
    re.I,
)
_OWNER = re.compile(
    r"owner[- ]occupied|owner occupancy|small landlord|natural person|"
    r"individual owner|owner of (?:no more than|fewer than)",
    re.I,
)


def parse_conditions(text: str | dict | None) -> list[Predicate]:
    """Turn coverage prose into predicates. Unrecognised prose yields nothing,
    which the evaluator treats as "no structured condition to test"."""
    if not text:
        return []
    if isinstance(text, dict):
        text = " ".join(f"{k}: {v}" for k, v in text.items())

    predicates: list[Predicate] = []

    if m := _CO_DATE.search(text):
        predicates.append(
            Predicate(
                kind="co_date",
                outcome_if_missing="unknown",
                requires="certificate_of_occupancy_date",
                co_date=dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                direction="on_or_before",
                describe=f"certificate of occupancy on or before {m.group(0)[-10:]}",
            )
        )
    elif _CO_LOOSE.search(text):
        predicates.append(
            Predicate(
                kind="co_unspecified",
                outcome_if_missing="unknown",
                requires="certificate_of_occupancy_date",
                describe="coverage turns on a certificate-of-occupancy date",
            )
        )

    if m := _BUILT_ON_BEFORE.search(text):
        predicates.append(
            Predicate(
                kind="year_built",
                outcome_if_missing="unknown",
                requires="year_built",
                year=int(m.group(1)),
                direction="on_or_before",
                describe=f"built on or before {m.group(1)}",
            )
        )
    elif m := _BUILT_AFTER.search(text):
        predicates.append(
            Predicate(
                kind="year_built",
                outcome_if_missing="unknown",
                requires="year_built",
                year=int(m.group(1)),
                direction="after",
                describe=f"built after {m.group(1)}",
            )
        )

    if m := _UNITS_AT_LEAST.search(text):
        predicates.append(
            Predicate(
                kind="units",
                outcome_if_missing="unknown",
                requires="units",
                count=int(m.group(1)),
                direction="at_least",
                describe=f"{m.group(1)} or more units",
            )
        )
    elif m := _UNITS_FEWER.search(text):
        count = int(m.group(1)) if m.group(1) else 3
        predicates.append(
            Predicate(
                kind="units",
                outcome_if_missing="unknown",
                requires="units",
                count=count,
                direction="fewer_than",
                describe=f"fewer than {count} units",
            )
        )

    if _OWNER.search(text):
        predicates.append(
            Predicate(
                kind="owner",
                outcome_if_missing="unknown",
                requires="owner_identity",
                describe="coverage or exemption turns on who owns the building",
            )
        )

    return predicates


# ------------------------------------------------------------ evaluation ---
def evaluate(predicates: list[Predicate], facts: AddressFacts) -> CoverageVerdict:
    """Conjunction over predicates, with unknown dominating a clean pass.

    Ordering matters: a definite failure beats an unknown (if the building is
    plainly outside the unit threshold, a missing owner name is irrelevant),
    but an unknown beats a pass.
    """
    reasons: list[str] = []
    unresolved: list[str] = []
    saw_unknown = False

    for pred in predicates:
        outcome, reason = _evaluate_one(pred, facts)
        reasons.append(reason)
        if outcome is Outcome.fails:
            return CoverageVerdict(Outcome.fails, reasons, unresolved)
        if outcome is Outcome.unknown:
            saw_unknown = True
            if pred.requires:
                unresolved.append(pred.requires)

    if saw_unknown:
        return CoverageVerdict(Outcome.unknown, reasons, sorted(set(unresolved)))
    return CoverageVerdict(Outcome.applies, reasons, [])


def _evaluate_one(pred: Predicate, facts: AddressFacts) -> tuple[Outcome, str]:
    if pred.kind == "owner":
        return (
            Outcome.unknown,
            "Owner identity is deliberately absent from the sample data, so "
            f"the condition ({pred.describe}) cannot be resolved.",
        )

    if pred.kind in {"co_date", "co_unspecified"}:
        if facts.year_built is None:
            return (
                Outcome.unknown,
                f"No year built, and {pred.describe}; cannot be resolved.",
            )
        if pred.co_date is None:
            return (Outcome.unknown, f"{pred.describe}; only year built is available.")
        # Year built is a proxy for the certificate date, and a wrong call here
        # is a scored error - so the cutoff year itself is always unknown.
        if facts.year_built < pred.co_date.year:
            return (
                Outcome.applies,
                f"Built {facts.year_built}, before the {pred.co_date.year} "
                "certificate-of-occupancy cutoff year.",
            )
        if facts.year_built > pred.co_date.year:
            return (
                Outcome.fails,
                f"Built {facts.year_built}, after the {pred.co_date.year} "
                "certificate-of-occupancy cutoff year.",
            )
        return (
            Outcome.unknown,
            f"Built in {facts.year_built}, the same year as the "
            f"{pred.co_date.isoformat()} certificate-of-occupancy cutoff. Year "
            "built is not the certificate date, so coverage is unknown.",
        )

    if pred.kind == "year_built":
        if facts.year_built is None:
            return (Outcome.unknown, f"No year built in the data; {pred.describe}.")
        if pred.direction == "on_or_before":
            ok = facts.year_built <= pred.year
        else:
            ok = facts.year_built > pred.year
        return (
            (Outcome.applies if ok else Outcome.fails),
            f"Built {facts.year_built}; condition is {pred.describe}.",
        )

    if pred.kind == "units":
        if facts.units is None:
            return (Outcome.unknown, f"No unit count in the data; {pred.describe}.")
        if pred.direction == "at_least":
            ok = facts.units >= pred.count
        else:
            ok = facts.units < pred.count
        return (
            (Outcome.applies if ok else Outcome.fails),
            f"{facts.units} units; condition is {pred.describe}.",
        )

    return (Outcome.unknown, f"Unhandled condition: {pred.describe}.")


class ExemptionOutcome(StrEnum):
    """Polarity here is inverted relative to coverage conditions."""

    exempt = "exempt"  # the exemption matches - the rule does NOT apply
    not_exempt = "not_exempt"  # the exemption definitively does not match
    unknown = "unknown"


@dataclass(slots=True)
class ExemptionVerdict:
    outcome: ExemptionOutcome
    reasons: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def evaluate_exemption(predicates: list[Predicate], facts: AddressFacts) -> ExemptionVerdict:
    """Decide whether an exemption carves this building out of a rule.

    An exemption is a conjunction, so one definitively false prong defeats the
    whole exemption even when another prong is unknowable. That is what lets us
    answer a small-landlord exception on a 32-unit building: whoever owns it,
    the exemption needs "2 or fewer units" and the building has 32, so the
    exemption cannot apply and the rule stands. The challenge asks for exactly
    this - explain why the exception can't apply rather than giving up.
    """
    if not predicates:
        return ExemptionVerdict(ExemptionOutcome.not_exempt)

    inner = evaluate(predicates, facts)
    if inner.outcome is Outcome.applies:
        return ExemptionVerdict(ExemptionOutcome.exempt, inner.reasons)
    if inner.outcome is Outcome.fails:
        return ExemptionVerdict(ExemptionOutcome.not_exempt, inner.reasons)
    return ExemptionVerdict(ExemptionOutcome.unknown, inner.reasons, inner.unresolved)


# ------------------------------------------------------- jurisdiction fit ---
def jurisdiction_matches(
    *, rule_jurisdiction: str, rule_level: str, facts: AddressFacts
) -> tuple[bool, str]:
    """Does a rule's jurisdiction cover this address?"""
    want = rule_jurisdiction.strip()
    if rule_level == "state":
        got = (facts.legal_state or "").upper()
        ok = want.upper() == got
        return ok, (f"State rule for {want}; address is in {got or 'an unknown state'}.")

    # City rule: "San Francisco, CA"
    if "," in want:
        city_part, _, state_part = want.rpartition(",")
        city_part, state_part = city_part.strip(), state_part.strip().upper()
    else:
        city_part, state_part = want, (facts.legal_state or "").upper()

    got_city = (facts.legal_city or "").strip()
    got_state = (facts.legal_state or "").upper()
    ok = city_part.casefold() == got_city.casefold() and state_part == got_state
    return ok, (
        f"City rule for {city_part}, {state_part}; address resolves to "
        f"{got_city or 'an unresolved city'}, {got_state or '??'}."
    )
