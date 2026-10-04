"""Three-valued fact checks against the compiled expression tree.

Everything here is pure: evidence in, verdict and trace out. No database, no
model, no clock. That is what lets a reported decision be replayed from its
inputs months later and come out identical.

The one judgement call worth naming is the certificate-of-occupancy proxy.
Several cutoffs in this corpus are certificate dates (San Francisco
1979-06-13, Los Angeles 1978-10-01) and the supplied data has only a year
built. A year built clearly outside the cutoff year settles the question - a
building from 1952 has no 1979 certificate and one from 2014 certainly does
not predate the cutoff - but a building *in* the cutoff year cannot be
settled, and the challenge says so explicitly. So the proxy is used only
outside the cutoff year, it is recorded in the trace when used, and inside the
cutoff year the answer is unknown.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from app.modules.address_lookup.rule_adapter.models import (
    NEVER_SUPPLIED,
    Atom,
    Expr,
    Field,
    Op,
)
from app.modules.address_lookup.rule_evaluation.decisions import (
    CheckTrace,
    Reason,
    Ternary,
    conjunction,
    disjunction,
)

#: Fact scopes that can prove a statement about a building. A unit-level or
#: parcel-level value cannot: a parcel with nine buildings says nothing about
#: the one a tenant lives in.
BUILDING_SCOPES = {"source_address_row", "building"}


@dataclass(slots=True)
class FactValue:
    """One typed fact with the status and provenance that came with it."""

    value: Any = None
    status: str = "not_supplied"  # present | not_supplied | invalid | conflicted
    scope: str = "source_address_row"
    provenance: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == "present" and self.value is not None


@dataclass(slots=True)
class AddressEvidence:
    """Everything the evaluator is allowed to know about one address."""

    address_id: str
    legal_city: FactValue
    legal_state: FactValue
    units: FactValue
    year_built: FactValue
    certificate_of_occupancy_date: FactValue
    use_code: FactValue
    jurisdiction_method: str | None = None
    place_geoid: str | None = None
    jurisdiction_note: str | None = None

    def get(self, name: Field) -> FactValue:
        return getattr(self, str(name), FactValue())


_STATUS_REASON = {
    "not_supplied": Reason.fact_not_supplied,
    "invalid": Reason.fact_invalid,
    "conflicted": Reason.fact_conflicted,
}

_OPS = {
    Op.eq: lambda a, b: a == b,
    Op.ne: lambda a, b: a != b,
    Op.lt: lambda a, b: a < b,
    Op.lte: lambda a, b: a <= b,
    Op.gt: lambda a, b: a > b,
    Op.gte: lambda a, b: a >= b,
    Op.is_true: lambda a, _: a is True,
    Op.is_false: lambda a, _: a is False,
}

_HUMAN_OP = {
    Op.eq: "is",
    Op.ne: "is not",
    Op.lt: "is under",
    Op.lte: "is at most",
    Op.gt: "is over",
    Op.gte: "is at least",
}


def _describe(atom: Atom) -> str:
    if atom.op in (Op.is_true, Op.is_false):
        want = "" if atom.op is Op.is_true else " not"
        return f"{str(atom.field).replace('_', ' ')} is{want} the case"
    value = atom.value.isoformat() if isinstance(atom.value, dt.date) else atom.value
    return f"{str(atom.field).replace('_', ' ')} {_HUMAN_OP[atom.op]} {value}"


def evaluate_atom(atom: Atom, evidence: AddressEvidence) -> CheckTrace:
    """One condition against one fact."""
    condition = _describe(atom)
    fact = evidence.get(atom.field)

    # A fact this corpus never carries. Said plainly, because "unknown" with no
    # reason reads like a bug and this one is a property of the data.
    if atom.field in NEVER_SUPPLIED:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.fact_never_supplied,
            detail=(
                f"The condition ({condition}) turns on a fact the supplied data "
                "deliberately omits, so it cannot be resolved for any address."
            ),
            condition_id=atom.id,
            source_span=atom.anchor.source_span if atom.anchor else None,
            field=str(atom.field),
            fact_status="never_supplied",
        )

    # A certificate-of-occupancy cutoff with no day in the source.
    if atom.field is Field.certificate_of_occupancy_date and atom.value == dt.date(1, 1, 1):
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.certificate_date_not_supplied,
            detail=(
                "Coverage turns on a certificate-of-occupancy date, and the source "
                "does not state the cutoff to the day."
            ),
            condition_id=atom.id,
            source_span=atom.anchor.source_span if atom.anchor else None,
            field=str(atom.field),
            fact_status="cutoff_not_stated",
        )

    if atom.field is Field.certificate_of_occupancy_date and not fact.usable:
        return _certificate_via_year_built(atom, evidence, condition)

    if not fact.usable:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=_STATUS_REASON.get(fact.status, Reason.fact_not_supplied),
            detail=(
                f"The condition ({condition}) needs "
                f"{str(atom.field).replace('_', ' ')}, which is {fact.status.replace('_', ' ')} "
                f"for this address."
            ),
            condition_id=atom.id,
            source_span=atom.anchor.source_span if atom.anchor else None,
            field=str(atom.field),
            fact_status=fact.status,
        )

    if fact.scope not in BUILDING_SCOPES:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.fact_wrong_scope,
            detail=(
                f"The only {str(atom.field).replace('_', ' ')} available describes a "
                f"{fact.scope.replace('_', ' ')}, which cannot settle a condition about "
                "this building."
            ),
            condition_id=atom.id,
            source_span=atom.anchor.source_span if atom.anchor else None,
            field=str(atom.field),
            fact_value=fact.value,
            fact_status=fact.status,
        )

    left, right = fact.value, atom.value
    if atom.field in (Field.legal_city, Field.legal_state, Field.use_code):
        left, right = str(left).strip().casefold(), str(right).strip().casefold()

    try:
        held = _OPS[atom.op](left, right)
    except TypeError:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.fact_invalid,
            detail=f"The condition ({condition}) could not be compared to {fact.value!r}.",
            condition_id=atom.id,
            field=str(atom.field),
            fact_value=fact.value,
            fact_status=fact.status,
        )

    shown = fact.value.isoformat() if isinstance(fact.value, dt.date) else fact.value
    return CheckTrace(
        check="condition",
        value=Ternary.true if held else Ternary.false,
        reason=Reason.fact_satisfied if held else Reason.fact_violated,
        detail=(
            f"{str(atom.field).replace('_', ' ').capitalize()} is {shown}; "
            f"the condition is that {condition}."
        ),
        condition_id=atom.id,
        source_span=atom.anchor.source_span if atom.anchor else None,
        field=str(atom.field),
        fact_value=fact.value,
        fact_status=fact.status,
    )


def _certificate_via_year_built(
    atom: Atom, evidence: AddressEvidence, condition: str
) -> CheckTrace:
    """Year built as a bounded proxy for a certificate date.

    Used only where it cannot be wrong: strictly outside the cutoff year. In
    the cutoff year the certificate could fall on either side of the cutoff
    day, so the answer stays unknown - which is what the challenge asks for.
    """
    cutoff: dt.date = atom.value
    year = evidence.year_built
    span = atom.anchor.source_span if atom.anchor else None

    if not year.usable:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.certificate_date_not_supplied,
            detail=(
                f"No certificate-of-occupancy date for this address, and year built is "
                f"{year.status.replace('_', ' ')}, so the {cutoff.isoformat()} cutoff "
                "cannot be resolved."
            ),
            condition_id=atom.id,
            source_span=span,
            field="certificate_of_occupancy_date",
            fact_status=year.status,
        )

    built = int(year.value)
    wants_on_or_before = atom.op in (Op.lte, Op.lt)

    if built == cutoff.year:
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.certificate_cutoff_year_ambiguous,
            detail=(
                f"Built in {built}, the same year as the {cutoff.isoformat()} "
                "certificate-of-occupancy cutoff. Year built is not the certificate "
                "date, so which side of the cutoff it falls on is unknown."
            ),
            condition_id=atom.id,
            source_span=span,
            field="year_built",
            fact_value=built,
            fact_status=year.status,
        )

    before = built < cutoff.year
    held = before if wants_on_or_before else not before
    return CheckTrace(
        check="condition",
        value=Ternary.true if held else Ternary.false,
        reason=Reason.year_built_proxy_used,
        detail=(
            f"Built {built}, {'before' if before else 'after'} the {cutoff.year} "
            f"certificate-of-occupancy cutoff year, so the {cutoff.isoformat()} cutoff is "
            f"{'met' if held else 'not met'}. Year built is used as a proxy only outside "
            "the cutoff year, where it cannot be wrong."
        ),
        condition_id=atom.id,
        source_span=span,
        field="year_built",
        fact_value=built,
        fact_status=year.status,
    )


def evaluate_expr(expr: Expr, evidence: AddressEvidence) -> tuple[Ternary, list[CheckTrace]]:
    """Evaluate a tree, collecting every check that was made."""
    traces: list[CheckTrace] = []
    values: list[Ternary] = []

    for child in expr.children:
        if isinstance(child, Atom):
            trace = evaluate_atom(child, evidence)
            traces.append(trace)
            values.append(trace.value)
        else:
            value, inner = evaluate_expr(child, evidence)
            traces.extend(inner)
            values.append(value)

    if not values:
        # An empty tree is vacuous: `all` of nothing is true, `any` of nothing
        # is false. Whether an empty *coverage* tree is legitimate is a review
        # question, settled before this point - not here.
        return (Ternary.true if expr.kind == "all" else Ternary.false), traces

    return (conjunction(values) if expr.kind == "all" else disjunction(values)), traces


def unresolved_fields(traces: list[CheckTrace]) -> list[str]:
    """Which facts would have to be supplied to settle an unknown."""
    out = []
    for trace in traces:
        if trace.value is Ternary.unknown and trace.field:
            name = trace.field
            if trace.reason is Reason.certificate_cutoff_year_ambiguous:
                name = "certificate_of_occupancy_date"
            if name not in out:
                out.append(name)
    return out
