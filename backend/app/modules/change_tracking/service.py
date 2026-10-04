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
    """A required source, mapping, or complete sample is unavailable."""


def load_tests() -> list[ChangeTest]:
    raw = json.loads(settings.change_tests.read_text())
    return [ChangeTest.model_validate(item) for item in raw]


def get_test(test_id: str) -> ChangeTest | None:
    return next((t for t in load_tests() if t.test_id == test_id), None)


def blocked_result(test: ChangeTest, reason: str) -> ChangeTestResult:
    """A case that could not be replayed, returned as a result that says so.

    Used only where a reader is better served by four answers and one stated
    reason than by nothing at all - which is the UI, and emphatically not the
    export. The empty affected set here means "not computed", so a caller that
    counts addresses must drop these rows rather than add their zeroes in.
    """
    return ChangeTestResult(
        test_id=test.test_id,
        title=test.title,
        type=test.type,
        as_of=test.as_of or test.as_of_after or dt.date.fromisoformat(settings.default_as_of),
        affected_address_ids=[],
        conflict_flag_address_ids=[],
        notes=reason,
        expected_behavior=test.expected_behavior,
        canonical_matches=[],
        detail={},
        rules_resolved=False,
        blocked_reason=reason,
    )


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


def _local_may_apply(rule: Rule, address: Address, day: dt.date) -> bool:
    """Only flag a local overlap within its verified city, including unknown coverage."""
    from app.modules.address_lookup.rule_evaluation.base import evaluate_geography
    from app.modules.address_lookup.rule_evaluation.decisions import Ternary

    evidence = lookup_service.evidence_for(address)
    geography, _ = evaluate_geography(lookup_service.compiled_for(rule), evidence)
    return geography is Ternary.true and _result_for(rule, address, day) in _LIVE


async def run_test(
    session: AsyncSession,
    test: ChangeTest,
    *,
    address_limit: int = 500,
    persist: bool = True,
) -> ChangeTestResult:
    if address_limit < 500 and persist:
        raise ChangeInputError("Partial address runs may not be persisted.")
    matches: list[CanonicalMatch] = []
    rules_by_canonical: dict[str, list[Rule]] = {}
    for canonical_id in test.rule_ids + test.conflict_with:
        match, rules = await resolve_canonical(session, canonical_id)
        if canonical_id in test.rule_ids:
            matches.append(match)
        rules_by_canonical[canonical_id] = rules

    missing = [cid for cid, rules in rules_by_canonical.items() if not rules]
    if missing:
        raise ChangeInputError(f"{test.test_id} has no verified rule for: {', '.join(missing)}")
    if test.type == "pending":
        incorrect = [
            r.team_rule_id
            for cid in test.rule_ids
            for r in rules_by_canonical[cid]
            if r.status != "pending"
        ]
        if incorrect:
            raise ChangeInputError(
                f"{test.test_id} requires pending records; check status of {', '.join(incorrect)}"
            )
    if len(test.rule_ids) > 1:
        identities = [
            (cid, r.team_rule_id) for cid in test.rule_ids for r in rules_by_canonical[cid]
        ]
        if len({rule_id for _, rule_id in identities}) != len(identities):
            raise ChangeInputError(
                f"{test.test_id} maps one extracted rule to multiple canonical IDs."
            )

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
        if any(r.status != "failed" for r in primary):
            raise ChangeInputError(f"{test.test_id} requires a failed measure record.")
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
        if still_applying or live_caps:
            raise ChangeInputError(
                f"{test.test_id} negative guardrail failed: failed measure applied at "
                f"{len(still_applying)} addresses; rent cap applied at {len(live_caps)}."
            )
        detail = {
            "addresses_examined": len(addresses),
            "unexpected_applications": still_applying,
            "unexpected_rent_caps": live_caps,
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
