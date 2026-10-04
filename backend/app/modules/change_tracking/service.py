"""Module C: report which addresses each supplied law-change case affects.

The tests in dev/change_tests.json refer to rules by the challenge's own ids
(`CA-ALG-01`, `HOB-ALG-01`, ...). Our extracted records carry our ids
(`r-0001`), so the two have to be bridged. We do that with an explicit
selector table rather than a fuzzy match, and every result reports which of
our rules each canonical id resolved to - so a judge can see the mapping and
an unmatched id is visible instead of silently producing an empty set.

Answers are computed by replaying Module B's evaluator at the relevant dates,
not by hard-coding the expected outcome.

Each case is answered on its own. A case whose inputs are missing comes back
`blocked`, with the reason, instead of raising - so one unextracted ordinance
cannot blank the other four cases, the export, and every dashboard that reads
them.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, replace

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.models import Address, ChangeResult, Document, Rule
from app.modules.address_lookup import service as lookup_service
from app.modules.change_tracking.schemas import (
    CanonicalMatch,
    ChangeTest,
    ChangeTestResult,
)
from app.modules.change_tracking.validation import check_entry, export_entry


@dataclass(frozen=True, slots=True)
class Selector:
    """How to find our extracted rule(s) for a canonical challenge rule id."""

    jurisdiction: str
    level: str
    category: str
    status: str | None = None
    source_doc_ids: tuple[str, ...] = ()

    def describe(self) -> str:
        base = f"{self.level} rule for {self.jurisdiction}, category {self.category}"
        if self.status:
            base += f", status {self.status}"
        if self.source_doc_ids:
            base += f", source {', '.join(self.source_doc_ids)}"
        return base


# The id scheme is JURISDICTION-CATEGORY-index, with a P prefix on the index
# for measures that never took effect.
CANONICAL_RULES: dict[str, Selector] = {
    "CA-ALG-01": Selector("CA", "state", "algorithmic_rent_setting", source_doc_ids=("D022",)),
    "NJ-ALG-01": Selector("NJ", "state", "algorithmic_rent_setting", source_doc_ids=("D069",)),
    "HOB-ALG-01": Selector(
        "Hoboken, NJ", "city", "algorithmic_rent_setting", source_doc_ids=("D093",)
    ),
    "JC-ALG-01": Selector(
        "Jersey City, NJ", "city", "algorithmic_rent_setting", source_doc_ids=("D088",)
    ),
    "MA-ALG-P1": Selector("MA", "state", "algorithmic_rent_setting", "pending", ("D089",)),
    "MA-ALG-P2": Selector("MA", "state", "algorithmic_rent_setting", "pending", ("D090",)),
    "MA-RENT-P1": Selector("MA", "state", "rent_increase_limits", "failed", ("D091",)),
}


class ChangeInputError(ValueError):
    """A run was requested that may not happen - e.g. persisting a truncated one.

    Missing data is not this error: it makes one case `blocked`, not the run.
    """


def load_tests() -> list[ChangeTest]:
    raw = json.loads(settings.change_tests.read_text())
    return [ChangeTest.model_validate(item) for item in raw]


def get_test(test_id: str) -> ChangeTest | None:
    return next((t for t in load_tests() if t.test_id == test_id), None)


async def resolve_canonical(
    session: AsyncSession, canonical_id: str
) -> tuple[CanonicalMatch, list[Rule]]:
    selector = CANONICAL_RULES.get(canonical_id)
    if selector is None:
        return (
            CanonicalMatch(
                canonical_id=canonical_id,
                selector="(no selector defined)",
                note="Unknown canonical id; add it to CANONICAL_RULES.",
            ),
            [],
        )

    stmt = (
        select(Rule)
        .options(selectinload(Rule.document))
        .where(
            Rule.jurisdiction.ilike(selector.jurisdiction),
            Rule.level == selector.level,
            Rule.category == selector.category,
        )
    )
    if selector.status:
        stmt = stmt.where(Rule.status == selector.status)
    if selector.source_doc_ids:
        stmt = stmt.where(Rule.source_doc_id.in_(selector.source_doc_ids))
    rules = list((await session.execute(stmt.order_by(Rule.team_rule_id))).scalars())

    note = None
    if not rules:
        note = (
            "No extracted rule matches this selector yet - run Module A "
            "extraction over the corpus first."
        )
    elif len(rules) > 1:
        note = "Multiple provisions from this source match; review the grouped obligations."

    source_ids = {r.source_doc_id for r in rules if r.source_doc_id}
    documents = {
        d.doc_id: d
        for d in (
            await session.execute(select(Document).where(Document.doc_id.in_(source_ids)))
        ).scalars()
    }
    from app.modules.rule_extraction.service import span_occurs_in

    verified_rules = [
        rule
        for rule in rules
        if (document := documents.get(rule.source_doc_id)) is not None
        and document.body
        and document.url == rule.source_url
        and span_occurs_in(rule.quoted_span, document.body)
    ]
    if len(verified_rules) != len(rules):
        note = "A matched rule has no stored source body or its quote/URL is unverified."
        rules = []

    return (
        CanonicalMatch(
            canonical_id=canonical_id,
            selector=selector.describe(),
            matched_rule_ids=[r.team_rule_id for r in rules],
            matched=bool(rules),
            note=note,
            sources=[
                {
                    "team_rule_id": r.team_rule_id,
                    "source_doc_id": r.source_doc_id,
                    "source_url": r.source_url,
                    "citation": r.citation,
                    "quoted_span": r.quoted_span,
                    "retrieved_at": (
                        documents[r.source_doc_id].retrieved_at
                        if r.source_doc_id in documents
                        else None
                    ),
                }
                for r in rules
            ],
        ),
        rules,
    )


async def _addresses_for(session: AsyncSession, states: list[str], limit: int) -> list[Address]:
    stmt = select(Address).options(selectinload(Address.jurisdiction)).order_by(Address.address_id)
    if states:
        stmt = stmt.where(Address.state.in_([s.upper() for s in states]))
    return list((await session.execute(stmt.limit(limit))).scalars())


def _result_for(rule: Rule, address: Address, day: dt.date) -> str:
    evidence = lookup_service.evidence_for(address)
    return lookup_service.evaluate_rule_for_address(rule, evidence, day).result


def _applies(rule: Rule, address: Address, day: dt.date) -> bool:
    return _result_for(rule, address, day) == "applies"


#: Results that mean the rule is not in force for this address on this day.
_NOT_IN_FORCE = {"not_yet_effective"}
#: Results that mean the temporal gate is open - the rule is law here now, even
#: if a missing fact leaves its coverage unresolved.
_LIVE = {"applies", "unknown", "superseded"}


def _gate_opened(
    rule: Rule, address: Address, before: dt.date, after: dt.date
) -> tuple[str, str] | None:
    """Did this rule start reaching this address between the two dates?

    Asking "did it move from applies to applies" would miss every address whose
    coverage is unresolved, and those are the majority here - a rule whose
    exemption turns on a fact the data lacks is `unknown`, not `applies`. The
    change being tested is temporal, so the question is whether the date gate
    opened for an address the rule could reach, and the answer distinguishes
    the two cases rather than averaging them.
    """
    was, now = _result_for(rule, address, before), _result_for(rule, address, after)
    if was in _NOT_IN_FORCE and now in _LIVE:
        return was, now
    return None


def _in_scope(rule: Rule, address: Address, as_of: dt.date) -> bool:
    """Jurisdiction and coverage reach this address, ignoring status and date -
    i.e. who *would* be affected if the measure took effect.

    Runs the same compiled rule and the same evaluator as a live lookup, with
    the temporal gate removed, so "would be affected" and "is affected" cannot
    drift apart. Unknown counts as in scope: the question is who might be
    covered, and the challenge treats an unresolved fact as a real answer
    rather than a no.
    """
    from app.modules.address_lookup.rule_evaluation.base import (
        evaluate_base,
        evaluate_geography,
    )
    from app.modules.address_lookup.rule_evaluation.decisions import BaseResult, Ternary

    compiled = lookup_service.compiled_for(rule)
    evidence = lookup_service.evidence_for(address)

    geo, _ = evaluate_geography(compiled, evidence)
    if geo is Ternary.false:
        return False

    # Evaluate as if the measure were in force, so only geography and coverage
    # decide. Everything else about the rule is unchanged.
    as_if = replace(compiled, status="in_force", effective_dates=())
    decision = evaluate_base(as_if, evidence, as_of)
    return decision.result in (BaseResult.applies, BaseResult.unknown)


def _geography(rule: Rule, address: Address):
    """Whether the rule's jurisdiction holds this address: a `Ternary`, which is
    `unknown` for a city rule when the address has no verified legal city."""
    from app.modules.address_lookup.rule_evaluation.base import evaluate_geography

    geography, _ = evaluate_geography(
        lookup_service.compiled_for(rule), lookup_service.evidence_for(address)
    )
    return geography


def _local_may_apply(rule: Rule, address: Address, day: dt.date) -> bool:
    """Only flag a local overlap within its verified city, including unknown coverage."""
    from app.modules.address_lookup.rule_evaluation.decisions import Ternary

    return _geography(rule, address) is Ternary.true and _result_for(rule, address, day) in _LIVE


_TEST_TYPES = ("as_of", "boundary", "pending", "negative")


def _undated(rules: list[Rule]) -> dict[str, str]:
    """The rules in a date case that cannot change between two dates, and why.

    Without this a date case whose rule has no usable effective date replays
    cleanly and moves nobody, which reads exactly like a finding. It is not
    one: the rule is `unknown` (or in force) on both dates for want of a date.
    """
    reasons: dict[str, str] = {}
    for rule in rules:
        compiled = lookup_service.compiled_for(rule)
        if compiled.status in ("pending", "failed"):
            reasons[rule.team_rule_id] = (
                f"is recorded as {compiled.status}, so no date makes it law"
            )
        elif compiled.effective_date_unresolved:
            recorded = f" {rule.effective_date}" if rule.effective_date else ""
            reasons[rule.team_rule_id] = (
                f"has an effective date{recorded} the source does not yet support, "
                "so it needs a reviewed effective date"
            )
        elif not compiled.effective_dates:
            reasons[rule.team_rule_id] = "has no effective date, so it reads the same on both dates"
    return reasons


def _blocked(
    test: ChangeTest,
    as_of: dt.date,
    matches: list[CanonicalMatch],
    reason: str,
    *,
    detail: dict | None = None,
) -> ChangeTestResult:
    """A case with no answer. Its empty sets mean "not computed", and every
    consumer has to read them that way - which is why `status` says so."""
    return ChangeTestResult(
        test_id=test.test_id,
        title=test.title,
        type=test.type,
        as_of=as_of,
        affected_address_ids=[],
        conflict_flag_address_ids=[],
        notes=f"Not computed: {reason}",
        expected_behavior=test.expected_behavior,
        canonical_matches=matches,
        detail=detail or {},
        rules_resolved=all(m.matched for m in matches),
        status="blocked",
        blocked_reason=reason,
    )


async def run_test(
    session: AsyncSession,
    test: ChangeTest,
    *,
    address_limit: int = 500,
) -> ChangeTestResult:
    """Replay one case. Missing or unverified inputs give a `blocked` result
    naming what is missing; nothing here raises for want of data."""
    as_of = test.as_of or test.as_of_after or dt.date.fromisoformat(settings.default_as_of)
    matches: list[CanonicalMatch] = []
    rules_by_canonical: dict[str, list[Rule]] = {}
    for canonical_id in dict.fromkeys(test.rule_ids + test.conflict_with):
        match, rules = await resolve_canonical(session, canonical_id)
        if canonical_id in test.rule_ids:
            matches.append(match)
        rules_by_canonical[canonical_id] = rules

    def blocked(reason: str, detail: dict | None = None) -> ChangeTestResult:
        return _blocked(test, as_of, matches, reason, detail=detail)

    if test.type not in _TEST_TYPES:
        return blocked(f"Unsupported test type {test.type!r}.")
    missing = [m for m in matches if not rules_by_canonical[m.canonical_id]]
    # A negative case's answer rests on the guardrail below, which runs with or
    # without the failed measure's own record; a missing record only makes the
    # answer partial. Every other case needs its rule to say anything at all.
    if missing and test.type != "negative":
        return blocked(
            "No verified rule for "
            + " ".join(
                f"{m.canonical_id} ({m.selector}): {(m.note or 'no match').rstrip('.')}."
                for m in missing
            )
        )
    if test.type == "pending":
        incorrect = [
            r.team_rule_id
            for cid in test.rule_ids
            for r in rules_by_canonical[cid]
            if r.status != "pending"
        ]
        if incorrect:
            return blocked(
                f"This case requires pending records; check the status of {', '.join(incorrect)}."
            )
    if len(test.rule_ids) > 1:
        identities = [
            (cid, r.team_rule_id) for cid in test.rule_ids for r in rules_by_canonical[cid]
        ]
        if len({rule_id for _, rule_id in identities}) != len(identities):
            return blocked("One extracted rule is mapped to more than one challenge rule id.")

    primary = [r for cid in test.rule_ids for r in rules_by_canonical[cid]]
    warnings: list[str] = []

    # A conflict partner that was never extracted leaves the affected set
    # standing; only the flags it would have raised are missing.
    absent_partners = [cid for cid in test.conflict_with if not rules_by_canonical[cid]]
    if absent_partners:
        warnings.append(
            f"Conflict check not run: no verified rule for {', '.join(absent_partners)}, "
            "so a possible conflict with it cannot be flagged."
        )

    if test.type == "as_of":
        if test.as_of_before is None or test.as_of_after is None:
            return blocked("The case definition has no as_of_before/as_of_after dates.")
        undated = _undated(primary)
        if len(undated) == len(primary):
            return blocked(
                "No rule in this case can change between "
                f"{test.as_of_before} and {test.as_of_after}: "
                + "; ".join(f"{rule_id} {why}" for rule_id, why in undated.items())
                + ".",
                detail={"undated_rules": undated},
            )
        warnings.extend(
            f"{rule_id} cannot change between the two dates: it {why}."
            for rule_id, why in undated.items()
        )

    addresses = await _addresses_for(session, test.states, address_limit)

    affected: list[str] = []
    conflicted: list[str] = []
    detail: dict = {}
    notes = ""

    if test.type == "as_of":
        before, after = test.as_of_before, test.as_of_after
        changed: list[str] = []
        now_applies: list[str] = []
        now_unknown: list[str] = []
        transitions: dict[str, dict[str, dict[str, str]]] = {}
        for address in addresses:
            opened = {
                r.team_rule_id: transition
                for r in primary
                if (transition := _gate_opened(r, address, before, after))
            }
            if not opened:
                continue
            changed.append(address.address_id)
            transitions[address.address_id] = {
                rule_id: {"before": was, "after": now} for rule_id, (was, now) in opened.items()
            }
            (
                now_applies if any(now == "applies" for _, now in opened.values()) else now_unknown
            ).append(address.address_id)
        affected = changed
        detail = {
            "as_of_before": before.isoformat(),
            "as_of_after": after.isoformat(),
            "addresses_examined": len(addresses),
            "covered_after": len(now_applies),
            "coverage_unresolved_after": len(now_unknown),
            "transitions": transitions,
            "rule_status": {
                r.team_rule_id: {
                    "status": r.status,
                    "effective_date": r.effective_date,
                }
                for r in primary
            },
        }
        records, compiled, relations = await lookup_service.compiled_rules(session)
        category_by_id = {r.team_rule_id: r.category for r in records}
        rule_sets: dict[str, dict[str, dict[str, str]]] = {}
        for address in addresses:
            if address.address_id not in transitions:
                continue
            evidence = lookup_service.evidence_for(address)
            snapshots = {}
            for label, day in (("before", before), ("after", after)):
                decisions = lookup_service.decide_for_address(
                    records, compiled, relations, evidence, day
                )
                snapshots[label] = {
                    d.team_rule_id: str(d.result)
                    for d in decisions
                    if category_by_id[d.team_rule_id] == "algorithmic_rent_setting"
                    and str(d.result) not in {"does_not_apply", "failed"}
                }
            rule_sets[address.address_id] = snapshots
        detail["rule_sets"] = rule_sets
        notes = (
            f"{len(changed)} of {len(addresses)} addresses in "
            f"{', '.join(test.states) or 'the sample'} move from not in force on "
            f"{before} to in force on {after}: {len(now_applies)} are covered outright and "
            f"{len(now_unknown)} have coverage this data cannot resolve."
        )

        # A local rule already in force where a later state rule arrives is a
        # preemption question a human has to settle - flag, do not guess.
        for conflict_id in test.conflict_with:
            for conflict_rule in rules_by_canonical.get(conflict_id, []):
                for address in addresses:
                    if address.address_id in transitions and _local_may_apply(
                        conflict_rule, address, before
                    ):
                        conflicted.append(address.address_id)
        conflicted = sorted(set(conflicted))
        if conflicted:
            notes += (
                f" {len(conflicted)} carry a conflict flag: a local ban is "
                "already in force and the new state law may or may not preempt it."
            )

    elif test.type == "boundary":
        # The question is where each ordinance reaches, so a building counts
        # when its *verified* legal city is the ordinance's city and the rule is
        # live there - including `unknown`, where only a building fact the data
        # lacks is unsettled. Counting `applies` alone would report an empty
        # city for every ordinance still awaiting coverage review. An address
        # with no verified legal city is never placed by its mailing line.
        from app.modules.address_lookup.rule_evaluation.decisions import Ternary

        per_rule: dict[str, list[str]] = {}
        split: dict[str, dict[str, list[str]]] = {}
        unplaced: set[str] = set()
        for canonical_id in test.rule_ids:
            covered: set[str] = set()
            unresolved: set[str] = set()
            for a in addresses:
                for r in rules_by_canonical[canonical_id]:
                    geography = _geography(r, a)
                    if geography is Ternary.unknown:
                        unplaced.add(a.address_id)
                        continue
                    if geography is Ternary.false:
                        continue
                    result = _result_for(r, a, as_of)
                    if result == "applies":
                        covered.add(a.address_id)
                    elif result in _LIVE:
                        unresolved.add(a.address_id)
            # One provision covering a building outright settles it.
            unresolved -= covered
            per_rule[canonical_id] = sorted(covered | unresolved)
            split[canonical_id] = {
                "applies": sorted(covered),
                "coverage_unresolved": sorted(unresolved),
            }
        affected = sorted({aid for ids in per_rule.values() for aid in ids})
        overlap = sorted(
            set.intersection(*(set(v) for v in per_rule.values())) if len(per_rule) > 1 else set()
        )
        detail = {
            "per_rule": per_rule,
            "per_rule_split": split,
            "overlap": overlap,
            "legal_city_unresolved": sorted(unplaced),
            "addresses_examined": len(addresses),
        }
        notes = "; ".join(
            f"{cid}: {len(per_rule[cid])} addresses inside its city "
            f"({len(parts['applies'])} covered outright, "
            f"{len(parts['coverage_unresolved'])} with coverage this data cannot resolve)"
            for cid, parts in split.items()
        )
        if overlap:
            notes += f". {len(overlap)} addresses matched more than one city rule."
        if unplaced:
            notes += (
                f". {len(unplaced)} addresses have no verified legal city and are left "
                "out rather than placed by their mailing city."
            )

    elif test.type == "pending":
        per_rule = {
            cid: sorted(
                {
                    a.address_id
                    for a in addresses
                    for r in rules_by_canonical[cid]
                    if _in_scope(r, a, as_of)
                }
            )
            for cid in test.rule_ids
        }
        affected = sorted({aid for ids in per_rule.values() for aid in ids})
        detail = {
            "in_force_now": False,
            "addresses_examined": len(addresses),
            "per_rule": per_rule,
            "rule_status": {r.team_rule_id: r.status for r in primary},
        }
        notes = (
            f"Pending, so no address is covered on {as_of}. If enacted, "
            f"{len(affected)} of {len(addresses)} sampled addresses would fall "
            "in scope."
        )

    elif test.type == "negative":
        # "No address has a live cap" says nothing when there were no
        # addresses to look at.
        if not addresses:
            return blocked(
                f"No sample address in {', '.join(test.states) or 'the sample'} was "
                "available to check, so an empty set would be vacuous."
            )
        if any(r.status != "failed" for r in primary):
            return blocked(
                "This case requires a failed measure record; check the status of "
                + ", ".join(r.team_rule_id for r in primary if r.status != "failed")
                + "."
            )
        still_applying = [
            a.address_id for a in addresses if any(_applies(r, a, as_of) for r in primary)
        ]
        affected = []
        ma_cap_rules = list(
            (
                await session.execute(
                    select(Rule)
                    .options(selectinload(Rule.document))
                    .where(
                        Rule.jurisdiction.in_(("MA", "Boston, MA", "Cambridge, MA")),
                        Rule.category == "rent_increase_limits",
                        # ch. 40P is a bar, not a cap. Unknown provenance must
                        # remain in the guardrail set rather than disappearing
                        # through SQL's NULL comparison semantics.
                        or_(Rule.source_doc_id != "D048", Rule.source_doc_id.is_(None)),
                    )
                )
            ).scalars()
        )
        live_caps = sorted(
            {
                a.address_id
                for a in addresses
                for r in ma_cap_rules
                if _result_for(r, a, as_of) in {"applies", "unknown", "superseded"}
            }
        )
        detail = {
            "addresses_examined": len(addresses),
            "unexpected_applications": still_applying,
            "unexpected_rent_caps": live_caps,
            "rule_status": {r.team_rule_id: r.status for r in primary},
        }
        detail["failed_record"] = bool(primary)
        if still_applying or live_caps:
            return blocked(
                f"Negative guardrail failed: the failed measure applies at "
                f"{len(still_applying)} addresses and a rent cap is live at "
                f"{len(live_caps)}, so an empty set would be false.",
                detail,
            )
        if primary:
            notes = (
                f"Measure failed, so the affected set is empty on {as_of}; none of the "
                f"{len(addresses)} addresses examined has a live rent cap."
            )
        else:
            notes = (
                f"None of the {len(addresses)} addresses examined has a live rent cap on "
                f"{as_of}, so the affected set is empty. The measure's failure is not yet "
                "backed by a captured record."
            )
            warnings.extend(
                f"No failed-measure record for {m.canonical_id} ({m.selector}) has been "
                "captured. The empty set rests on the rent-cap check alone."
                for m in missing
            )

    return ChangeTestResult(
        test_id=test.test_id,
        title=test.title,
        type=test.type,
        as_of=as_of,
        affected_address_ids=affected,
        conflict_flag_address_ids=conflicted,
        notes=notes,
        expected_behavior=test.expected_behavior,
        canonical_matches=matches,
        detail=detail,
        rules_resolved=all(m.matched for m in matches),
        status="partial" if warnings else "complete",
        warnings=warnings,
    )


def _held_to_export(result: ChangeTestResult, address_ids: set[str]) -> ChangeTestResult:
    """Judge a computed case by the rules the export applies, so a case the
    page shows as answered is never one the export would leave out."""
    if result.status == "blocked":
        return result
    invalid, incomplete = check_entry(result.test_id, export_entry(result), address_ids=address_ids)
    if invalid:
        detail = dict(result.detail)
        if result.affected_address_ids:
            detail["withheld_affected_address_ids"] = result.affected_address_ids
        if result.conflict_flag_address_ids:
            detail["withheld_conflict_flag_address_ids"] = result.conflict_flag_address_ids
        return result.model_copy(
            update={
                "status": "blocked",
                "blocked_reason": " ".join(invalid),
                "affected_address_ids": [],
                "conflict_flag_address_ids": [],
                "detail": detail,
            }
        )
    if incomplete:
        return result.model_copy(
            update={"status": "partial", "warnings": [*result.warnings, *incomplete]}
        )
    return result


async def _persist(session: AsyncSession, result: ChangeTestResult) -> None:
    existing = (
        await session.execute(
            select(ChangeResult).where(
                ChangeResult.test_id == result.test_id, ChangeResult.as_of == result.as_of
            )
        )
    ).scalar_one_or_none()
    row = existing or ChangeResult(test_id=result.test_id, as_of=result.as_of)
    row.test_type = result.type
    row.affected_address_ids = result.affected_address_ids
    row.conflict_flag_address_ids = result.conflict_flag_address_ids
    row.notes = result.notes
    row.detail = result.detail
    session.add(row)
    await session.flush()


async def run_tests(
    session: AsyncSession,
    tests: list[ChangeTest],
    *,
    address_limit: int = 500,
    persist: bool = False,
    address_ids: set[str] | None = None,
) -> list[ChangeTestResult]:
    """Every case, each answered independently - the one entry point for the
    routes and the freeze script.

    A full run is held to the export's rules case by case. A truncated run is
    exploration: it can never be exported, so it is not judged as if it could.
    Blocked cases are never persisted.
    """
    if address_limit < 500 and persist:
        raise ChangeInputError("Partial address runs may not be persisted.")
    full = address_limit >= 500
    if full and address_ids is None:
        address_ids = set((await session.execute(select(Address.address_id))).scalars())
    results: list[ChangeTestResult] = []
    for test in tests:
        result = await run_test(session, test, address_limit=address_limit)
        if full and address_ids is not None:
            result = _held_to_export(result, address_ids)
        if persist and result.status != "blocked":
            await _persist(session, result)
        results.append(result)
    return results
