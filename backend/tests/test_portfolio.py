"""The roll-up a portfolio page is given instead of the thing it rolls up.

An account is set up holding the buildings it is answerable for, which is the
whole imported book rather than the handful somebody types in. The page that
answers "what binds my portfolio" used to fetch every outcome for every one of
them and count in the browser: 500 buildings against 115 rules is 57,500
outcomes carrying their checks, their explanations and their quoted spans - 75
MB of JSON, ten seconds, and a page that never painted. Stripping the audit
trail only reached 17 MB.

So the counting moved to the server and the page gets 200 KB. These tests are
about the two things that can go wrong with that move: the arithmetic being
different from what the browser used to compute, and the cache handing back an
answer that is no longer true.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.modules.accounts.schemas import Role
from app.modules.address_lookup import service
from app.modules.address_lookup.schemas import LookupResponse, RuleOutcome

AS_OF = dt.date(2026, 10, 1)


class FakeAddress:
    def __init__(self, address_id: str):
        self.address_id = address_id


def outcome(
    rule: str,
    result: str,
    *,
    category: str = "rent",
    fields: list[str] | None = None,
    in_jurisdiction: bool = True,
    checks: list[dict] | None = None,
    title: str | None = None,
) -> RuleOutcome:
    return RuleOutcome(
        team_rule_id=rule,
        result=result,
        explanation=f"{rule} {result}",
        conflict_flag=False,
        unresolved_fields=fields or [],
        in_jurisdiction=in_jurisdiction,
        category=category,
        jurisdiction="Los Angeles",
        level="city",
        status="in_force",
        title=title or f"Rule {rule}",
        key_value=None,
        citation=f"§ {rule}",
        source_url=None,
        quoted_span=None,
        superseded_by=None,
        issue_key=None,
        checks=checks or [],
    )


def answer(address_id: str, outcomes: list[RuleOutcome]) -> LookupResponse:
    return LookupResponse(
        address_id=address_id,
        as_of=AS_OF,
        legal_city="Los Angeles",
        legal_state="CA",
        resolution_method="geocoder",
        outcomes=outcomes,
        applies_count=sum(1 for o in outcomes if o.result == "applies"),
        unknown_count=sum(1 for o in outcomes if o.result == "unknown"),
    )


@pytest.fixture(autouse=True)
def _empty_cache():
    service.clear_portfolio_cache()
    yield
    service.clear_portfolio_cache()


def stub(monkeypatch, answers: dict[str, LookupResponse], counter: list | None = None):
    async def lookup_address(
        session, address, as_of, *, persist=False, include_not_applicable=False
    ):
        if counter is not None:
            counter.append(address.address_id)
        return answers[address.address_id]

    monkeypatch.setattr(service, "lookup_address", lookup_address)


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


async def test_the_matrix_counts_binding_and_unsettled_per_category(monkeypatch):
    stub(
        monkeypatch,
        {
            "A1": answer(
                "A1",
                [
                    outcome("r-1", "applies", category="rent"),
                    outcome("r-2", "applies", category="rent"),
                    outcome("r-3", "unknown", category="rent", fields=["units"]),
                    outcome("r-4", "applies", category="eviction"),
                ],
            )
        },
    )

    result = await service.portfolio(None, [FakeAddress("A1")], AS_OF)

    [building] = result.buildings
    assert building.applies == 3
    assert building.unknown == 1
    assert building.by_category["rent"].binding == 2
    assert building.by_category["rent"].unsettled == 1
    assert building.by_category["eviction"].binding == 1
    # Only the categories with something in them, so a wide matrix stays small.
    assert set(building.by_category) == {"rent", "eviction"}
    assert result.categories == ["eviction", "rent"]


async def test_the_queue_is_ordered_by_answers_not_buildings(monkeypatch):
    """A field blocking forty answers at one building is a bigger job than one
    blocking a single answer at six, and only that ordering says what to chase."""
    stub(
        monkeypatch,
        {
            "A1": answer(
                "A1",
                [outcome(f"r-{n}", "unknown", fields=["year_built"]) for n in range(5)],
            ),
            "A2": answer("A2", [outcome("r-9", "unknown", fields=["units"])]),
            "A3": answer("A3", [outcome("r-9", "unknown", fields=["units"])]),
        },
    )

    result = await service.portfolio(
        None, [FakeAddress("A1"), FakeAddress("A2"), FakeAddress("A3")], AS_OF
    )

    assert [b.field for b in result.blocking] == ["year_built", "units"]
    assert result.blocking[0].answers == 5
    assert result.blocking[0].buildings == 1
    assert result.blocking[1].answers == 2
    assert result.blocking[1].buildings == 2
    assert result.totals.fully_answered == 0


async def test_a_rule_that_misses_is_counted_once_not_once_per_building(monkeypatch):
    """The compliance fact is "tested against 312 of yours and misses all of
    them", not thirty-five thousand rows saying it one at a time."""
    answers = {f"A{n}": answer(f"A{n}", [outcome("r-cap", "does_not_apply")]) for n in range(1, 21)}
    stub(monkeypatch, answers)

    result = await service.portfolio(None, [FakeAddress(f"A{n}") for n in range(1, 21)], AS_OF)

    assert len(result.missed) == 1
    [missed] = result.missed
    assert missed.rule == "r-cap"
    assert missed.buildings == 20
    # Named enough to go and look at one; not a second copy of the portfolio.
    assert len(missed.address_ids) == service.NAMED
    assert result.totals.not_binding == 20


async def test_an_exemption_is_kept_apart_from_a_rule_that_simply_misses(monkeypatch):
    """Two different compliance facts. "We are exempt" and "it never reached
    us" are not the same sentence to put in front of a regulator."""
    stub(
        monkeypatch,
        {
            "A1": answer(
                "A1",
                [
                    outcome(
                        "r-exempt",
                        "does_not_apply",
                        checks=[
                            {
                                "check": "exemption",
                                "value": "true",
                                "detail": "Owner-occupied, two units.",
                            }
                        ],
                    ),
                    outcome("r-missed", "does_not_apply"),
                ],
            )
        },
    )

    result = await service.portfolio(None, [FakeAddress("A1")], AS_OF)

    assert [r.rule for r in result.exemptions] == ["r-exempt"]
    assert result.exemptions[0].why == "Owner-occupied, two units."
    assert [r.rule for r in result.missed] == ["r-missed"]


async def test_a_rule_from_another_city_is_not_a_miss(monkeypatch):
    """ "A Berkeley ordinance does not cover your Boston building" is a map, not
    compliance information - and there are eighty of them per address."""
    stub(
        monkeypatch,
        {
            "A1": answer(
                "A1",
                [
                    outcome("r-here", "does_not_apply", in_jurisdiction=True),
                    outcome("r-elsewhere", "does_not_apply", in_jurisdiction=False),
                ],
            )
        },
    )

    result = await service.portfolio(None, [FakeAddress("A1")], AS_OF)

    assert [r.rule for r in result.missed] == ["r-here"]
    assert result.totals.not_binding == 1


async def test_a_rule_nobody_could_turn_into_a_condition_is_counted(monkeypatch):
    """Such a rule cannot be tested, so neither its coverage nor its exemptions
    can be ruled out - and a page claiming "no exemption matched" has to say so."""
    unmapped = [{"check": "coverage", "value": "unknown", "reason": "rule_clause_unmapped"}]
    stub(
        monkeypatch,
        {
            "A1": answer("A1", [outcome("r-prose", "unknown", checks=unmapped)]),
            "A2": answer("A2", [outcome("r-prose", "unknown", checks=unmapped)]),
        },
    )

    result = await service.portfolio(None, [FakeAddress("A1"), FakeAddress("A2")], AS_OF)

    # Counted per rule, not per building: one rule is untestable, twice over.
    assert result.totals.untranslated_rules == 1


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


async def test_the_second_render_of_a_page_is_not_paid_for(monkeypatch):
    seen: list[str] = []
    stub(monkeypatch, {"A1": answer("A1", [outcome("r-1", "applies")])}, counter=seen)

    await service.portfolio(None, [FakeAddress("A1")], AS_OF)
    await service.portfolio(None, [FakeAddress("A1")], AS_OF)

    assert seen == ["A1"]


async def test_adding_a_building_is_a_different_portfolio(monkeypatch):
    """The key is what went into the answer, so a changed set cannot read a
    stale one - there is nothing to invalidate."""
    seen: list[str] = []
    stub(
        monkeypatch,
        {
            "A1": answer("A1", [outcome("r-1", "applies")]),
            "A2": answer("A2", [outcome("r-1", "applies")]),
        },
        counter=seen,
    )

    first = await service.portfolio(None, [FakeAddress("A1")], AS_OF)
    second = await service.portfolio(None, [FakeAddress("A1"), FakeAddress("A2")], AS_OF)

    assert first.totals.buildings == 1
    assert second.totals.buildings == 2
    assert seen == ["A1", "A1", "A2"]


async def test_a_different_date_is_a_different_answer(monkeypatch):
    seen: list[str] = []
    stub(monkeypatch, {"A1": answer("A1", [outcome("r-1", "applies")])}, counter=seen)

    await service.portfolio(None, [FakeAddress("A1")], AS_OF)
    await service.portfolio(None, [FakeAddress("A1")], dt.date(2025, 1, 1))

    assert len(seen) == 2


async def test_recompiling_the_rules_drops_the_roll_ups(monkeypatch):
    """A portfolio is an answer about a set of buildings *and* a set of rules.
    The TTL would get there eventually; five minutes of a page insisting on the
    old answer is five minutes too many."""
    seen: list[str] = []
    stub(monkeypatch, {"A1": answer("A1", [outcome("r-1", "applies")])}, counter=seen)

    await service.portfolio(None, [FakeAddress("A1")], AS_OF)
    service.clear_compiled_cache()
    await service.portfolio(None, [FakeAddress("A1")], AS_OF)

    assert len(seen) == 2


# ---------------------------------------------------------------------------
# Who the import belongs to
# ---------------------------------------------------------------------------


def test_only_the_role_that_is_answerable_for_buildings_adopts_the_import():
    """A housing provider is answerable for buildings and the book is exactly
    that set, so it is theirs on arrival. The other three are not a shorter
    version of the same list: a renter has one home and it is wherever they
    actually live, an advocate's cases are each a different person's situation,
    and an agency holds no addresses at all."""
    from app.modules.accounts.service import ADOPTS_THE_IMPORT

    assert ADOPTS_THE_IMPORT == {Role.provider}
