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
from dataclasses import dataclass, field
from typing import Any

from app.modules.address_lookup.rule_adapter.models import (
    NEVER_SUPPLIED,
    Atom,
    Basis,
    Expr,
    Field,
    Op,
    Origin,
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
    #: `Basis.presumed` when the record implies the fact without stating it.
    basis: str | None = None

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
    # Read from the parcel description by derived_facts.derive().
    use_description: FactValue = field(default_factory=FactValue)
    property_type: FactValue = field(default_factory=FactValue)
    units_floor: FactValue = field(default_factory=FactValue)
    building_is_subsidised: FactValue = field(default_factory=FactValue)
    seasonal_rental: FactValue = field(default_factory=FactValue)

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


def evaluate_atom(
    atom: Atom,
    evidence: AddressEvidence,
    *,
    role: Origin = Origin.coverage,
    as_of: dt.date | None = None,
) -> CheckTrace:
    """One condition against one fact, carrying the atom's basis into the trace.

    `role` says whether the atom sits in the coverage or the exemption tree:
    a presumed fact may only rule an exemption *out*, so the same fact can
    settle an exemption and still leave coverage unknown.
    """
    trace = _evaluate_atom(atom, evidence, role, as_of)
    if atom.basis.is_low and trace.basis is None:
        trace.basis = str(atom.basis)
    return trace


def _evaluate_atom(
    atom: Atom, evidence: AddressEvidence, role: Origin, as_of: dt.date | None
) -> CheckTrace:
    condition = _describe(atom)
    fact = evidence.get(atom.field)

    if atom.field in MEMBERSHIP_PROXIES:
        return _membership_via_year_built(atom, evidence, condition)
    if atom.field is Field.years_since_certificate_of_occupancy:
        return _years_since_certificate(atom, evidence, condition, as_of)
    if atom.field is Field.owner_unit_count and (
        bound := _owner_holds_more(atom, evidence, role, condition)
    ):
        return bound
    if (
        atom.field is Field.units
        and not fact.usable
        and (floor := _units_floor(atom, evidence, role, condition))
    ):
        return floor

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
    if atom.field in (Field.legal_city, Field.legal_state, Field.use_code, Field.property_type):
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
    if fact.basis == PRESUMED and (held or role is not Origin.exemption):
        # A presumption may defeat an exemption, nothing more: it never brings
        # a building inside an exemption, and never decides coverage.
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.fact_not_supplied,
            detail=(
                f"The condition ({condition}) needs {str(atom.field).replace('_', ' ')}, "
                f"which the {fact.provenance or 'record'} implies but does not state."
            ),
            condition_id=atom.id,
            source_span=atom.anchor.source_span if atom.anchor else None,
            field=str(atom.field),
            fact_status="presumed",
        )
    return CheckTrace(
        check="condition",
        value=Ternary.true if held else Ternary.false,
        reason=Reason.fact_satisfied if held else Reason.fact_violated,
        detail=(
            f"{str(atom.field).replace('_', ' ').capitalize()} is {shown}"
            + (f", presumed from the {fact.provenance}" if fact.basis == PRESUMED else "")
            + f"; the condition is that {condition}."
        ),
        condition_id=atom.id,
        source_span=atom.anchor.source_span if atom.anchor else None,
        field=str(atom.field),
        fact_value=fact.value,
        fact_status=fact.status,
        basis=fact.basis,
    )


PRESUMED = str(Basis.presumed)


@dataclass(frozen=True, slots=True)
class _Membership:
    """An ordinance whose reach the brief ties to a certificate-of-occupancy date."""

    name: str
    cutoff: dt.date
    #: What a building certificated after the cutoff is: Los Angeles's RSO
    #: covers only pre-cutoff buildings; San Francisco's ordinance still
    #: covers newer units for eviction, so membership there stays unknown.
    after: Ternary
    authority: str


MEMBERSHIP_PROXIES = {
    Field.los_angeles_rso_membership: _Membership(
        "the Los Angeles RSO",
        dt.date(1978, 10, 1),
        Ternary.false,
        "LAHD: the RSO applies to rental properties first built on or before October 1, 1978",
    ),
    Field.san_francisco_rent_ordinance_membership: _Membership(
        "the San Francisco Rent Ordinance",
        dt.date(1979, 6, 13),
        Ternary.unknown,
        "SF Rent Board: units first certificated after June 13, 1979 are only partly covered",
    ),
}


def _membership_via_year_built(atom: Atom, evidence: AddressEvidence, condition: str) -> CheckTrace:
    """Ordinance membership read from year built against the ordinance's cutoff.

    Before the cutoff year a building is inside; in the cutoff year it is
    unknown, exactly as for a certificate cutoff; after it, whatever the
    ordinance says of newer buildings. Low confidence throughout: membership
    has exceptions of its own that no column here records.
    """
    proxy = MEMBERSHIP_PROXIES[atom.field]
    span = atom.anchor.source_span if atom.anchor else None
    year = evidence.year_built
    if not year.usable:
        member, detail = (
            Ternary.unknown,
            (
                f"Whether this building is under {proxy.name} turns on its certificate-of-"
                f"occupancy date, and year built is {year.status.replace('_', ' ')}."
            ),
        )
        reason = Reason.certificate_date_not_supplied
    else:
        built = int(year.value)
        if built == proxy.cutoff.year:
            member, reason = Ternary.unknown, Reason.certificate_cutoff_year_ambiguous
            detail = (
                f"Built in {built}, the year of {proxy.name}'s {proxy.cutoff.isoformat()} "
                "cutoff, so which side of it the building falls on is unknown."
            )
        else:
            member = Ternary.true if built < proxy.cutoff.year else proxy.after
            reason = (
                Reason.membership_proxy_used
                if member is not Ternary.unknown
                else Reason.fact_not_supplied
            )
            detail = (
                f"Built {built}, {'before' if built < proxy.cutoff.year else 'after'} "
                f"{proxy.name}'s {proxy.cutoff.isoformat()} cutoff, so it is "
                + {
                    Ternary.true: "taken to be under the ordinance",
                    Ternary.false: "taken to be outside it",
                    Ternary.unknown: "not settled whether it is under the ordinance",
                }[member]
                + f" ({proxy.authority})."
            )
    value = member
    if atom.op is Op.is_false and member is not Ternary.unknown:
        value = Ternary.false if member is Ternary.true else Ternary.true
    return CheckTrace(
        check="condition",
        value=value,
        reason=reason,
        detail=detail,
        condition_id=atom.id,
        source_span=span,
        field="year_built",
        fact_value=year.value if year.usable else None,
        fact_status=year.status,
        basis=str(Basis.proxy),
    )


def _years_since_certificate(
    atom: Atom, evidence: AddressEvidence, condition: str, as_of: dt.date | None
) -> CheckTrace:
    """The rolling "certificate issued within the previous N years" test.

    The certificate date, if supplied, settles it. Otherwise year built bounds
    the certificate to that calendar year, which settles it unless the N-year
    line falls inside that year - then it is unknown, like a fixed cutoff.
    """
    span = atom.anchor.source_span if atom.anchor else None
    certificate, year = evidence.certificate_of_occupancy_date, evidence.year_built
    if as_of is None or not (certificate.usable or year.usable):
        return CheckTrace(
            check="condition",
            value=Ternary.unknown,
            reason=Reason.certificate_date_not_supplied,
            detail=(
                f"The condition ({condition}) needs a certificate-of-occupancy date or year built."
            ),
            condition_id=atom.id,
            source_span=span,
            field="certificate_of_occupancy_date",
            fact_status=year.status,
        )
    try:
        line = as_of.replace(year=as_of.year - int(atom.value))
    except ValueError:  # 29 February
        line = as_of.replace(year=as_of.year - int(atom.value), day=28)
    if certificate.usable:
        earliest = latest = certificate.value
    else:
        earliest, latest = dt.date(int(year.value), 1, 1), dt.date(int(year.value), 12, 31)

    def holds(day: dt.date) -> bool:
        # N or more years since the certificate <=> certificate on or before the line.
        return {
            Op.gte: day <= line,
            Op.gt: day < line,
            Op.lte: day >= line,
            Op.lt: day > line,
        }.get(atom.op, False)

    first, last = holds(earliest), holds(latest)
    value = (
        Ternary.true
        if first and last
        else Ternary.false
        if not (first or last)
        else Ternary.unknown
    )
    shown = earliest.isoformat() if certificate.usable else f"built {year.value}"
    return CheckTrace(
        check="condition",
        value=value,
        reason=(
            Reason.certificate_cutoff_year_ambiguous
            if value is Ternary.unknown
            else Reason.fact_satisfied
            if value is Ternary.true
            else Reason.fact_violated
        ),
        detail=(
            f"Certificate of occupancy {shown}; on {as_of.isoformat()} the condition is that "
            f"{condition} (the line is {line.isoformat()})"
            + (
                ", which falls inside the year built, so it is unknown."
                if value is Ternary.unknown
                else "."
            )
        ),
        condition_id=atom.id,
        source_span=span,
        field="certificate_of_occupancy_date" if certificate.usable else "year_built",
        fact_value=certificate.value if certificate.usable else year.value,
        fact_status="present",
    )


def _owner_holds_more(
    atom: Atom, evidence: AddressEvidence, role: Origin, condition: str
) -> CheckTrace | None:
    """An owner holds at least the units of this building.

    So "the owner has at most four units" is false for a 32-unit building,
    whoever the owner is - the small-landlord case the brief asks about. A
    presumed unit floor may settle it only inside an exemption.
    """
    units, floor = evidence.units, evidence.units_floor
    if units.usable and units.scope in BUILDING_SCOPES:
        held, basis, source = int(units.value), None, f"{units.value} units"
    elif floor.usable and role is Origin.exemption:
        held, basis, source = (
            int(floor.value),
            PRESUMED,
            f"at least {floor.value} units ({floor.provenance})",
        )
    else:
        return None
    limit = int(atom.value)
    exceeded = {Op.lte: held > limit, Op.lt: held >= limit, Op.eq: held > limit}.get(atom.op)
    if not exceeded:
        return None
    return CheckTrace(
        check="condition",
        value=Ternary.false,
        reason=Reason.fact_violated,
        detail=(
            f"This building alone has {source}, so its owner holds more than the condition "
            f"allows ({condition}), whoever the owner is."
        ),
        condition_id=atom.id,
        source_span=atom.anchor.source_span if atom.anchor else None,
        field="units",
        fact_value=held,
        fact_status="present",
        basis=basis,
    )


def _units_floor(
    atom: Atom, evidence: AddressEvidence, role: Origin, condition: str
) -> CheckTrace | None:
    """A unit count the parcel record bounds from below, used to defeat an
    exemption for small buildings - and for nothing else."""
    floor = evidence.units_floor
    if role is not Origin.exemption or not floor.usable:
        return None
    least, limit = int(floor.value), int(atom.value)
    if not {Op.lte: least > limit, Op.lt: least >= limit, Op.eq: least > limit}.get(atom.op):
        return None
    return CheckTrace(
        check="condition",
        value=Ternary.false,
        reason=Reason.fact_violated,
        detail=(
            f"The {floor.provenance} gives at least {least} units, so the exemption's "
            f"condition ({condition}) cannot hold."
        ),
        condition_id=atom.id,
        source_span=atom.anchor.source_span if atom.anchor else None,
        field="units",
        fact_value=least,
        fact_status="presumed",
        basis=PRESUMED,
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


def evaluate_expr(
    expr: Expr,
    evidence: AddressEvidence,
    *,
    role: Origin = Origin.coverage,
    as_of: dt.date | None = None,
) -> tuple[Ternary, list[CheckTrace]]:
    """Evaluate a tree, collecting every check that was made."""
    traces: list[CheckTrace] = []
    values: list[Ternary] = []

    for child in expr.children:
        if isinstance(child, Atom):
            trace = evaluate_atom(child, evidence, role=role, as_of=as_of)
            traces.append(trace)
            values.append(trace.value)
        else:
            value, inner = evaluate_expr(child, evidence, role=role, as_of=as_of)
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
