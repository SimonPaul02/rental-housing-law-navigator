"""An address somebody typed, and the line it must not cross.

The account is set up with an address book - the buildings it is answerable
for, imported with a year built and a unit count each. A renter's home is
wherever they actually live, which may not be in it, so they can type one and
keep it. Everybody can ask about any address without keeping it.

The thing that has to hold is that a typed row is counted nowhere. It is not in
the agency's denominator, not in lookups.json, not in the set a change case
scans, and not findable by search. `test_every_roll_up_asks_whether_a_row_was_
imported` is the guard: it compiles the statements each of those paths builds
and fails if one of them forgot, which is the only failure mode here that is
silent.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.modules.accounts.schemas import Role
from app.modules.address_lookup import service
from app.modules.address_lookup.address_resolution.models import ResolvedAddress
from app.modules.assistant import graph as agent
from app.modules.assistant import tools

# ---------------------------------------------------------------------------
# Reading what was typed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (
            "415 Mission St, San Francisco, CA 94105, United States",
            ("415 MISSION ST", "San Francisco", "CA", "94105"),
        ),
        ("415 Mission St, San Francisco, CA", ("415 MISSION ST", "San Francisco", "CA", None)),
        (
            "22 Precita Av, San Francisco, CA 94110, USA",
            ("22 PRECITA AV", "San Francisco", "CA", "94110"),
        ),
        # Case is normalised the way the imported rows carry it, so a typed
        # address dedupes against itself however it was written.
        ("1200 wilshire blvd, los angeles, ca", ("1200 WILSHIRE BLVD", "Los Angeles", "CA", None)),
    ],
)
def test_a_typed_address_is_read_as_street_city_state(typed, expected):
    assert service.parse_typed(typed) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "Mission St",  # no city, no state
        "415 Mission St, San Francisco",  # no state
        "415 Mission St, San Francisco, 94105",  # a postcode is not a state
        "",
    ],
)
def test_an_address_without_a_state_is_refused_with_the_shape_it_wants(typed):
    """The resolver needs a state and city names repeat across them.

    Refused rather than guessed: "Springfield" is in four of the states this
    corpus touches, and picking one would resolve to a real jurisdiction and a
    confidently wrong answer.
    """
    with pytest.raises(service.TypedAddressError) as raised:
        service.parse_typed(typed)
    # Whichever way it is short, the message says what shape is wanted.
    assert "state" in str(raised.value)


# ---------------------------------------------------------------------------
# What a resolved typed address is
# ---------------------------------------------------------------------------


def _resolved(address_id: str) -> ResolvedAddress:
    return ResolvedAddress(
        address_id=address_id,
        input=None,
        status="resolved",
        legal_state="CA",
        legal_county="San Francisco",
        legal_city="San Francisco",
        city_geoid="0667000",
        longitude=-122.3968,
        latitude=37.7903,
        matched_address="415 MISSION ST, SAN FRANCISCO, CA, 94105",
        benchmark="Public_AR_Current",
        vintage="Current_Current",
        resolution_method="geocoder",
    )


async def test_a_typed_address_is_resolved_but_not_stored(monkeypatch):
    """Transient on purpose: asking about an address leaves no row behind.

    Both callers want the same object and different things from it - the
    assistant answers a question about a building nobody is keeping, and
    `add_typed_place` adds this very object to a session.
    """
    monkeypatch.setattr(service, "_resolve_batch", lambda inputs: [_resolved(inputs[0].address_id)])

    row = await service.resolve_typed("415 Mission St, San Francisco, CA 94105")

    assert row.imported is False
    assert row.street_address == "415 MISSION ST"
    assert row.jurisdiction.legal_city == "San Francisco"
    assert row.jurisdiction.method == "geocoder"
    # No facts, and none invented. Every rule turning on one answers "unknown"
    # and names the field, which is the honest result rather than a guess.
    assert row.year_built is None
    assert row.units is None


async def test_a_typed_id_is_not_guessable_and_survives_normalising(monkeypatch):
    """`/lookup/{id}` answers for these rows, so a countable id would let
    somebody walk the list of places people live. Upper case because every
    caller normalises an id - a model writes `a0001` as readily as `A0001`."""
    monkeypatch.setattr(service, "_resolve_batch", lambda inputs: [_resolved(inputs[0].address_id)])

    ids = {
        (await service.resolve_typed("415 Mission St, San Francisco, CA")).address_id
        for _ in range(8)
    }

    assert len(ids) == 8
    for address_id in ids:
        assert address_id.startswith("U")
        assert address_id == address_id.upper()
        assert len(address_id) <= 16


# ---------------------------------------------------------------------------
# The line: counted nowhere
# ---------------------------------------------------------------------------


class _Result:
    """Enough of a SQLAlchemy result for the roll-ups to run against."""

    def __init__(self, rows):
        self._rows = rows

    def scalar_one(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []

    def __iter__(self):
        return iter(self._rows)


class _RecordingSession:
    """Records every statement, so the test can read what was asked for."""

    def __init__(self):
        self.statements = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _Result([])


def _sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": False}))


async def test_every_roll_up_asks_whether_a_row_was_imported():
    """The silent failure this whole flag exists to prevent.

    A new query over `addresses` that forgets the filter does not break
    anything visibly: it moves a denominator. The agency's page would report
    501 of 501 one day and 503 the next, and the difference would be the homes
    three renters typed in. Nothing else in the suite would notice, so this
    compiles what the stats actually ask for and insists the column is in each
    one.
    """
    from app.modules.address_lookup.router import address_stats

    session = _RecordingSession()
    await address_stats(session)

    touching = [_sql(st) for st in session.statements if "addresses" in _sql(st)]
    assert len(touching) >= 8, "the stats build a statement per figure"
    unfiltered = [sql for sql in touching if "imported" not in sql]
    assert unfiltered == [], (
        f"these count addresses without asking whether the row was imported: {unfiltered}"
    )


#: The two places in Module B that answer for one named building, and so must
#: reach a renter's own home as readily as an imported row. Everything else
#: that queries `addresses` is a roll-up and has to filter.
ANSWERS_FOR_ONE_BUILDING = {"lookup_batch", "lookup_one"}


def test_no_route_queries_addresses_without_deciding_about_the_import():
    """Reading the router rather than running it, because its query functions
    take `Query(...)` defaults and cannot be called directly - and because the
    regression to catch is somebody adding a *new* query, which no amount of
    exercising the current ones would find.
    """
    import ast
    import inspect

    from app.modules.address_lookup import router as module

    tree = ast.parse(inspect.getsource(module))
    offenders = []
    # Top-level routes only. A helper nested inside one is covered by whatever
    # its enclosing route decided - `address_stats` builds its filter once and
    # closes over it.
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        body = ast.dump(node)
        if "'Address'" not in body or "func=Name(id='select'" not in body:
            continue
        decides = "_book" in body or "imported" in body
        if not decides and node.name not in ANSWERS_FOR_ONE_BUILDING:
            offenders.append(node.name)
    assert offenders == [], (
        f"{offenders} query addresses without saying whether typed rows belong. "
        "Wrap the statement in `_book(...)`, or add it to "
        "ANSWERS_FOR_ONE_BUILDING if it answers for one named building."
    )


async def test_the_change_cases_scan_the_imported_stock_only():
    """ "184 of 250 CA addresses move" is a statement about the import.

    A renter's own home joining the denominator would make the same case report
    a different number to different people.
    """
    from app.modules.change_tracking import service as changes

    session = _RecordingSession()
    await changes._addresses_for(session, ["CA"], 500)

    assert "imported" in _sql(session.statements[0])


async def test_the_submission_export_is_the_import_only():
    """A person's own address belongs in no file this project hands in."""
    import inspect

    source = inspect.getsource(service.run_lookup_export)
    assert "Address.imported" in source


async def test_search_does_not_reach_an_address_somebody_typed():
    """Theirs to answer about, not everybody's to find."""
    session = _RecordingSession()
    deps = tools.Deps(session, None, Role.renter, dt.date(2026, 10, 1))

    await tools.BY_NAME["search_addresses"].run(deps, {"query": "Mission St"})

    assert session.statements
    for statement in session.statements:
        assert "imported" in _sql(statement)


# ---------------------------------------------------------------------------
# Who is offered what
# ---------------------------------------------------------------------------


def test_every_role_can_ask_about_an_address_nobody_holds():
    """Answering is not keeping. An agency holds no addresses at all and still
    has to be able to ask what applies at one."""
    for role in Role:
        assert "rules_for_any_address" in {tool.name for tool in tools.roster(role)}


@pytest.mark.parametrize(
    ("role", "allowed"),
    [(Role.renter, True), (Role.provider, False), (Role.advocate, False)],
)
async def test_only_a_renter_is_offered_a_building_that_was_never_imported(role, allowed):
    """Not permission - the endpoint serves anyone, because every row it writes
    is the caller's own. It is what the list is *for*: a renter's one home is
    wherever they live, and the other two hold books of buildings somebody is
    answerable for, which a typed row would not be."""

    async def nothing(event, data):
        return None

    ctx = agent.Session(session=None, principal=None, role=role, emit=nothing)
    tool = tools.BY_NAME["ask_to_add_building"]
    block = {"id": "toolu_1", "name": tool.name, "input": {"message": "Which?", "query": ""}}

    card, problem = await agent._card(ctx, tool, block)

    assert problem is None
    assert card["allow_new"] is allowed


async def test_an_address_the_geocoder_cannot_place_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(
        service,
        "_resolve_batch",
        lambda inputs: [
            ResolvedAddress(
                address_id=inputs[0].address_id,
                input=None,
                status="needs_review",
                legal_state=None,
                legal_county=None,
                legal_city=None,
                city_geoid=None,
                longitude=None,
                latitude=None,
                matched_address=None,
                benchmark=None,
                vintage=None,
                resolution_method="unresolved",
            )
        ],
    )
    deps = tools.Deps(None, None, Role.agency, dt.date(2026, 10, 1))

    answer = await tools.BY_NAME["rules_for_any_address"].run(
        deps, {"address": "999 Nowhere Rd, Nowheresville, ZZ", "as_of": ""}
    )

    assert answer["resolved"] is False
    assert "no legal city" in answer["error"]
    # No outcomes at all, rather than a set computed against no jurisdiction.
    assert "counts" not in answer
