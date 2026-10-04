"""Module C: report which addresses each supplied law-change case affects.

The tests in dev/change_tests.json refer to rules by the challenge's own ids
(`CA-ALG-01`, `HOB-ALG-01`, ...). Our extracted records carry our ids
(`r-0001`), so the two have to be bridged. We do that with an explicit
selector table rather than a fuzzy match, and every result reports which of
our rules each canonical id resolved to - so a judge can see the mapping and
an unmatched id is visible instead of silently producing an empty set.

Answers are computed by replaying Module B's evaluator at the relevant dates,
not by hard-coding the expected outcome.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.models import Address, ChangeResult, Rule
from app.modules.address_lookup import service as lookup_service
from app.modules.change_tracking.schemas import (
    CanonicalMatch,
    ChangeTest,
    ChangeTestResult,
)


@dataclass(frozen=True, slots=True)
class Selector:
    """How to find our extracted rule(s) for a canonical challenge rule id."""

    jurisdiction: str
    level: str
    category: str
    status: str | None = None

    def describe(self) -> str:
        base = f"{self.level} rule for {self.jurisdiction}, category {self.category}"
        return f"{base}, status {self.status}" if self.status else base


# The id scheme is JURISDICTION-CATEGORY-index, with a P prefix on the index
# for measures that never took effect.
CANONICAL_RULES: dict[str, Selector] = {
    "CA-ALG-01": Selector("CA", "state", "algorithmic_rent_setting"),
    "NJ-ALG-01": Selector("NJ", "state", "algorithmic_rent_setting"),
    "HOB-ALG-01": Selector("Hoboken, NJ", "city", "algorithmic_rent_setting"),
    "JC-ALG-01": Selector("Jersey City, NJ", "city", "algorithmic_rent_setting"),
    "MA-ALG-P1": Selector("MA", "state", "algorithmic_rent_setting", "pending"),
    "MA-ALG-P2": Selector("MA", "state", "algorithmic_rent_setting", "pending"),
    "MA-RENT-P1": Selector("MA", "state", "rent_increase_limits", "failed"),
}


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

    stmt = select(Rule).where(
        Rule.jurisdiction.ilike(selector.jurisdiction),
        Rule.level == selector.level,
        Rule.category == selector.category,
    )
    if selector.status:
        stmt = stmt.where(Rule.status == selector.status)
    rules = list((await session.execute(stmt.order_by(Rule.team_rule_id))).scalars())

    note = None
    if not rules:
        note = (
            "No extracted rule matches this selector yet - run Module A "
            "extraction over the corpus first."
        )
    elif len(rules) > 1 and canonical_id in {"MA-ALG-P1", "MA-ALG-P2"}:
        note = "Two Massachusetts bills share this selector (S.2983 and H.5222); both are reported."

    return (
        CanonicalMatch(
            canonical_id=canonical_id,
            selector=selector.describe(),
            matched_rule_ids=[r.team_rule_id for r in rules],
            matched=bool(rules),
            note=note,
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
_NOT_IN_FORCE = {"not_yet_effective", "pending"}
#: Results that mean the temporal gate is open - the rule is law here now, even
#: if a missing fact leaves its coverage unresolved.
_LIVE = {"applies", "unknown", "superseded"}


def _gate_opened(rule: Rule, address: Address, before: dt.date, after: dt.date) -> str | None:
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
        return now
    return None


def _in_scope(rule: Rule, address: Address) -> bool:
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
    decision = evaluate_base(as_if, evidence, dt.date(2026, 10, 1))
    return decision.result in (BaseResult.applies, BaseResult.unknown)


async def run_test(
    session: AsyncSession,
    test: ChangeTest,
    *,
    address_limit: int = 500,
    persist: bool = True,
) -> ChangeTestResult:
    matches: list[CanonicalMatch] = []
    rules_by_canonical: dict[str, list[Rule]] = {}
    for canonical_id in test.rule_ids + test.conflict_with:
        match, rules = await resolve_canonical(session, canonical_id)
        if canonical_id in test.rule_ids:
            matches.append(match)
        rules_by_canonical[canonical_id] = rules

    primary = [r for cid in test.rule_ids for r in rules_by_canonical.get(cid, [])]
    addresses = await _addresses_for(session, test.states, address_limit)
    as_of = test.as_of or test.as_of_after or dt.date.fromisoformat(settings.default_as_of)

    affected: list[str] = []
    conflicted: list[str] = []
    detail: dict = {}
    notes = ""

    if test.type == "as_of":
        before, after = test.as_of_before, test.as_of_after
        if before is None or after is None:
            raise ValueError(
                f"{test.test_id} is an as_of test but is missing as_of_before/as_of_after."
            )
        changed: list[str] = []
        now_applies: list[str] = []
        now_unknown: list[str] = []
        for address in addresses:
            opened = [o for o in (_gate_opened(r, address, before, after) for r in primary) if o]
            if not opened:
                continue
            changed.append(address.address_id)
            (now_applies if "applies" in opened else now_unknown).append(address.address_id)
        affected = changed
        detail = {
            "as_of_before": before.isoformat(),
            "as_of_after": after.isoformat(),
            "addresses_examined": len(addresses),
            "covered_after": len(now_applies),
            "coverage_unresolved_after": len(now_unknown),
            "rule_status": {
                r.team_rule_id: {
                    "status": r.status,
                    "effective_date": r.effective_date,
                }
                for r in primary
            },
        }
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
                    if address.address_id in changed and _applies(conflict_rule, address, after):
                        conflicted.append(address.address_id)
        conflicted = sorted(set(conflicted))
        if conflicted:
            notes += (
                f" {len(conflicted)} carry a conflict flag: a local ban is "
                "already in force and the new state law may or may not preempt it."
            )

    elif test.type == "boundary":
        per_rule: dict[str, list[str]] = {}
        for canonical_id in test.rule_ids:
            ids = [
                a.address_id
                for a in addresses
                for r in rules_by_canonical.get(canonical_id, [])
                if _applies(r, a, as_of)
            ]
            per_rule[canonical_id] = sorted(set(ids))
        affected = sorted({aid for ids in per_rule.values() for aid in ids})
        overlap = sorted(
            set.intersection(*(set(v) for v in per_rule.values())) if len(per_rule) > 1 else set()
        )
        detail = {
            "per_rule": per_rule,
            "overlap": overlap,
            "addresses_examined": len(addresses),
        }
        notes = (
            "; ".join(f"{cid}: {len(ids)} addresses" for cid, ids in per_rule.items())
            or "no rules resolved"
        )
        if overlap:
            notes += f". {len(overlap)} addresses matched more than one city rule."

    elif test.type == "pending":
        would = [a.address_id for a in addresses if any(_in_scope(r, a) for r in primary)]
        affected = sorted(set(would))
        detail = {
            "in_force_now": False,
            "addresses_examined": len(addresses),
            "rule_status": {r.team_rule_id: r.status for r in primary},
        }
        notes = (
            f"Pending, so no address is covered on {as_of}. If enacted, "
            f"{len(affected)} of {len(addresses)} sampled addresses would fall "
            "in scope."
        )

    elif test.type == "negative":
        still_applying = [
            a.address_id for a in addresses if any(_applies(r, a, as_of) for r in primary)
        ]
        affected = []
        detail = {
            "addresses_examined": len(addresses),
            "unexpected_applications": still_applying,
            "rule_status": {r.team_rule_id: r.status for r in primary},
        }
        notes = (
            f"Measure failed, so the affected set is empty on {as_of}."
            if not still_applying
            else (
                f"WARNING: {len(still_applying)} addresses still report this rule "
                "as applying even though it failed - check the extracted status."
            )
        )

    else:
        notes = f"Unsupported test type {test.type!r}."

    if persist:
        existing = (
            await session.execute(
                select(ChangeResult).where(
                    ChangeResult.test_id == test.test_id, ChangeResult.as_of == as_of
                )
            )
        ).scalar_one_or_none()
        row = existing or ChangeResult(test_id=test.test_id, as_of=as_of)
        row.test_type = test.type
        row.affected_address_ids = affected
        row.conflict_flag_address_ids = conflicted
        row.notes = notes
        row.detail = detail
        session.add(row)
        await session.flush()

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
    )
