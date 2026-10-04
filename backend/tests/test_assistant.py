"""The assistant's loop, its pause, and the two invariants that keep it honest.

No network and no database. The model is a scripted stub that returns the turns
a real one would, and the account's own rows are stubbed at
`accounts.service` - so what these assert is the graph's behaviour, not that
Anthropic is up or that Postgres is seeded.

Two of these tests exist to catch a regression that would cost money rather
than correctness, and they are the reason the file is worth reading:
`test_system_prompt_is_identical_for_two_people` and
`test_cache_breakpoint_moves_to_the_newest_turn`. The system prompt and the
tool list are the cached prefix of every request; a name or a date interpolated
into either one invalidates it on every turn, silently, and nothing else in the
suite would notice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from itertools import count

import pytest

from app.core.auth import Principal
from app.core.config import settings
from app.modules.accounts.schemas import Role
from app.modules.assistant import graph as agent
from app.modules.assistant import prompts, service, tools
from app.modules.assistant.schemas import ChatRequest, Resolution

PRINCIPAL = Principal(user_id="user_01TEST", session_id="session_01TEST")
_ids = count(1)


# ---------------------------------------------------------------------------
# A scripted model.
# ---------------------------------------------------------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeMessage:
    content: list[dict]
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)

    def to_dict(self, mode: str = "python") -> dict:
        return {"content": self.content}


class FakeStream:
    def __init__(self, message: FakeMessage):
        self._message = message

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *_) -> bool:
        return False

    @property
    def text_stream(self):
        async def gen():
            for block in self._message.content:
                if block.get("type") == "text":
                    yield block["text"]

        return gen()

    async def get_final_message(self) -> FakeMessage:
        return self._message


class FakeClient:
    """Replays a script of turns, and records every request body it was sent."""

    def __init__(self, script: list[FakeMessage]):
        self.script = list(script)
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **kwargs) -> FakeStream:
        self.requests.append(kwargs)
        message = self.script.pop(0) if self.script else FakeMessage([text("(script ran out)")])
        return FakeStream(message)


def text(body: str) -> dict:
    return {"type": "text", "text": body}


def call(name: str, **args) -> dict:
    return {
        "type": "tool_use",
        "id": f"toolu_{next(_ids):04d}",
        "name": name,
        "input": args,
    }


# ---------------------------------------------------------------------------
# The caller's own rows.
# ---------------------------------------------------------------------------


@dataclass
class FakePlace:
    id: int = 7
    address_id: str = "A0132"
    label: str | None = None
    note: str | None = None
    street_address: str = "1200 Wilshire Blvd"
    postal_city: str = "Van Nuys"
    state: str = "CA"
    year_built: int | None = 1975
    units: int | None = 24
    legal_city: str | None = "Los Angeles"
    legal_state: str | None = "CA"
    jurisdiction_status: str = "resolved"
    postal_city_differs: bool = True
    contract_count: int = 0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime(2026, 9, 1, 12, 0))


@dataclass
class FakeContract:
    id: int = 3
    place_id: int = 7
    filename: str = "lease.pdf"
    kind: str = "PDF"
    unit_label: str | None = None
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    monthly_rent_cents: int | None = 185000
    note: str | None = None


@dataclass
class FakeAccount:
    name: str | None = "Ada"
    role: Role = Role.renter


@pytest.fixture
def world(monkeypatch):
    """One account, one saved building, one lease - swappable per test."""
    state = {"places": [FakePlace()], "contracts": [FakeContract()], "account": FakeAccount()}

    async def places(session, principal):
        return list(state["places"])

    async def contracts(session, principal, place_id=None):
        return list(state["contracts"])

    async def read(session, principal):
        return state["account"]

    for module in (service, agent, tools):
        monkeypatch.setattr(module.accounts, "places", places, raising=False)
        monkeypatch.setattr(module.accounts, "contracts", contracts, raising=False)
        monkeypatch.setattr(module.accounts, "read", read, raising=False)
    return state


@dataclass
class Recorder:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def __call__(self, event: str, data: dict) -> None:
        self.events.append((event, data))

    def of(self, kind: str) -> list[dict]:
        return [data for event, data in self.events if event == kind]

    @property
    def said(self) -> str:
        return "".join(data["text"] for data in self.of("delta"))


def arrange(monkeypatch, script: list[FakeMessage], *, role: Role = Role.renter):
    client = FakeClient(script)
    monkeypatch.setattr(agent.llm, "get_client", lambda: client)
    recorder = Recorder()
    ctx = agent.Session(session=None, principal=PRINCIPAL, role=role, emit=recorder)
    return ctx, client, recorder


def thread_id() -> str:
    return f"t{next(_ids)}"


async def transcript(ctx: agent.Session, thread: str) -> list[dict]:
    state = await agent.build(ctx).aget_state(service._config(ctx, thread))
    return (state.values or {}).get("messages") or []


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def test_a_plain_answer_streams_and_is_recorded(monkeypatch, world):
    ctx, client, heard = arrange(monkeypatch, [FakeMessage([text("Your building is in LA.")])])
    thread = thread_id()

    result = await service.run(ctx, ChatRequest(thread_id=thread, text="Where am I?"))

    assert heard.said == "Your building is in LA."
    assert len(client.requests) == 1
    assert result["cards"] == []
    # One call in, one out, and the cost of it priced from the catalogue.
    assert result["usage"]["input"] == 100
    assert result["usage"]["usd"] > 0

    turns = await service.thread(ctx, thread)
    assert [t["role"] for t in turns.turns] == ["user", "assistant"]
    assert turns.turns[0]["text"] == "Where am I?"


async def test_a_read_tool_runs_itself_and_the_answer_continues(monkeypatch, world):
    ctx, client, heard = arrange(
        monkeypatch,
        [
            FakeMessage([call("my_buildings")], stop_reason="tool_use"),
            FakeMessage([text("You have one: 1200 Wilshire Blvd.")]),
        ],
    )

    await service.run(ctx, ChatRequest(thread_id=thread_id(), text="What do I have saved?"))

    # Two model calls, and the second one carries the result of the first.
    assert len(client.requests) == 2
    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(results) == 1
    assert "1200 Wilshire Blvd" in results[0]["content"]
    assert heard.of("tool")[0]["name"] == "my_buildings"
    assert heard.said == "You have one: 1200 Wilshire Blvd."


async def test_the_loop_stops_at_the_step_cap(monkeypatch, world):
    """A model that never stops asking is stopped, rather than billed."""
    ctx, client, heard = arrange(
        monkeypatch,
        [FakeMessage([call("my_buildings")], stop_reason="tool_use") for _ in range(20)],
    )
    thread = thread_id()

    await service.run(ctx, ChatRequest(thread_id=thread, text="go"))

    assert len(client.requests) == settings.assistant_max_steps
    # It says so rather than going quiet, and says it without another call.
    assert "I stopped there" in heard.said

    # And the transcript it leaves is one the next turn can be appended to.
    # Cut off straight after the model, it would end on `tool_use` blocks
    # nobody answered; cut off straight after the tools, on two user messages
    # in a row. The API refuses both, and every later turn would be a 400.
    messages = await transcript(ctx, thread)
    assert messages[-1]["role"] == "assistant"
    assert not _unanswered(messages)
    roles = [m["role"] for m in messages]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))


def _unanswered(messages: list[dict]) -> set[str]:
    """Tool calls in the transcript with no `tool_result` anywhere after them."""
    called, answered = set(), set()
    for message in messages:
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                called.add(block["id"])
            elif block.get("type") == "tool_result":
                answered.add(block["tool_use_id"])
    return called - answered


async def test_a_parked_turn_is_the_only_unanswered_call(monkeypatch, world):
    """The one state where an unanswered `tool_use` is correct, and intended.

    It is the resume point: the next request supplies its result. Every other
    way of leaving one is a conversation that can never be continued.
    """
    ctx, _, _ = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("ask_to_add_building", message="Which?", query="")], stop_reason="tool_use"
            )
        ],
    )
    thread = thread_id()
    result = await service.run(ctx, ChatRequest(thread_id=thread, text="help"))

    messages = await transcript(ctx, thread)
    assert _unanswered(messages) == {result["cards"][0]["tool_use_id"]}


# ---------------------------------------------------------------------------
# The pause: a turn that stops to ask
# ---------------------------------------------------------------------------


async def test_an_ask_renders_a_card_and_leaves_the_turn_open(monkeypatch, world):
    world["places"] = []
    ctx, client, heard = arrange(
        monkeypatch,
        [
            FakeMessage(
                [
                    text("I need to know where you live."),
                    call(
                        "ask_to_add_building", message="Which building do you live in?", query="LA"
                    ),
                ],
                stop_reason="tool_use",
            )
        ],
    )
    thread = thread_id()

    result = await service.run(ctx, ChatRequest(thread_id=thread, text="What applies to me?"))

    # The control reached the browser, and the model was not called again.
    assert len(client.requests) == 1
    card = result["cards"][0]
    assert card["kind"] == "ask_to_add_building"
    assert card["message"] == "Which building do you live in?"
    assert card["query"] == "LA"
    assert heard.of("card") == result["cards"]

    # And the transcript ends on an unanswered tool_use, which is what makes
    # the next request a resumption rather than a new question.
    messages = await transcript(ctx, thread)
    assert messages[-1]["role"] == "assistant"
    assert any(b.get("type") == "tool_use" for b in messages[-1]["content"])


async def test_using_the_control_resumes_the_same_turn(monkeypatch, world):
    world["places"] = []
    ctx, client, _ = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("ask_to_add_building", message="Which one?", query="")],
                stop_reason="tool_use",
            ),
            FakeMessage([text("Los Angeles rules reach it.")]),
        ],
    )
    thread = thread_id()
    opened = await service.run(ctx, ChatRequest(thread_id=thread, text="What applies to me?"))

    # The browser saved the building against the endpoint that already exists,
    # then told the chat what it did.
    world["places"] = [FakePlace()]
    result = await service.run(
        ctx,
        ChatRequest(
            thread_id=thread,
            resolutions=[
                Resolution(
                    tool_use_id=opened["cards"][0]["tool_use_id"],
                    kind="ask_to_add_building",
                    value={"address_id": "A0132"},
                )
            ],
        ),
    )

    assert result["cards"] == []
    answer = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    # Written from the row, not from the browser's claim: the street and the
    # legal city are ours, and nothing in the request said them.
    assert len(answer) == 1
    assert "1200 Wilshire Blvd" in answer[0]["content"]
    assert "Los Angeles" in answer[0]["content"]


async def test_a_resolution_is_checked_against_the_account(monkeypatch, world):
    """A claim to have saved something that is not there is reported as such."""
    world["places"] = []
    ctx, client, _ = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("ask_to_add_building", message="Which one?", query="")],
                stop_reason="tool_use",
            ),
            FakeMessage([text("Nothing was added.")]),
        ],
    )
    thread = thread_id()
    opened = await service.run(ctx, ChatRequest(thread_id=thread, text="help"))

    await service.run(
        ctx,
        ChatRequest(
            thread_id=thread,
            resolutions=[
                Resolution(
                    tool_use_id=opened["cards"][0]["tool_use_id"],
                    kind="ask_to_add_building",
                    # Never saved - and `world["places"]` is still empty.
                    value={"address_id": "A9999"},
                )
            ],
        ),
    )

    answer = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ][0]
    assert "No building A9999 is saved" in answer["content"]


async def test_ignoring_a_control_is_answered_too(monkeypatch, world):
    """An unanswered `tool_use` would make the next request a 400."""
    ctx, client, _ = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("ask_to_pick_building", message="Which building?")], stop_reason="tool_use"
            ),
            FakeMessage([text("Fine - ask me again when you know.")]),
        ],
        role=Role.provider,
    )
    thread = thread_id()
    await service.run(ctx, ChatRequest(thread_id=thread, text="check my rent"))

    await service.run(ctx, ChatRequest(thread_id=thread, text="never mind, something else"))

    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(results) == 1
    assert "did not use that control" in results[0]["content"]


# ---------------------------------------------------------------------------
# What the model may not talk us into
# ---------------------------------------------------------------------------


async def test_a_document_cannot_be_asked_for_against_somebody_elses_building(monkeypatch, world):
    ctx, client, heard = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("ask_for_document", message="Upload it", address_id="A0404")],
                stop_reason="tool_use",
            ),
            FakeMessage([text("That is not one of yours.")]),
        ],
    )

    result = await service.run(ctx, ChatRequest(thread_id=thread_id(), text="here is my lease"))

    # No control was rendered, and the model was told why so it can recover.
    assert result["cards"] == []
    assert heard.of("card") == []
    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert results[0]["is_error"] is True
    assert "not one of their saved buildings" in results[0]["content"]


async def test_a_tool_this_role_does_not_have_is_answered_not_dropped(monkeypatch, world):
    """Every `tool_use` must leave `act` with a result or a place in `pending`.

    A renter has one home, so they are never given the building picker. If a
    call to it were dropped instead of answered, the `tool_use` would sit in
    the transcript unanswered and every later turn of that conversation would
    be a 400 - which is how a chat breaks permanently from one stray token.
    """
    ctx, client, _ = arrange(
        monkeypatch,
        [
            FakeMessage([call("ask_to_pick_building", message="Which?")], stop_reason="tool_use"),
            FakeMessage([text("You only have one home.")]),
        ],
        role=Role.renter,
    )
    thread = thread_id()

    await service.run(ctx, ChatRequest(thread_id=thread, text="which building?"))

    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "not a tool you have" in results[0]["content"]
    # And no user message in the transcript is empty, which would be a 400 too.
    for message in await transcript(ctx, thread):
        assert message["content"] != []


@pytest.mark.parametrize(
    "href",
    ["https://evil.example/steal", "//evil.example", "/rules/../../etc/passwd", "javascript:x"],
)
async def test_only_this_apps_own_paths_can_be_offered(monkeypatch, world, href):
    ctx, client, _ = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("show_view", message="Try this", href=href, label="Open")],
                stop_reason="tool_use",
            ),
            FakeMessage([text("Sorry.")]),
        ],
    )

    result = await service.run(ctx, ChatRequest(thread_id=thread_id(), text="where do I look?"))

    assert result["cards"] == []
    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert results[0]["is_error"] is True


async def test_a_link_card_is_an_offer_and_does_not_stop_the_turn(monkeypatch, world):
    """A link needs no answer, so the agent is told it was shown and finishes.

    The alternative - parking the turn on a click that may never come - leaves
    the conversation unanswerable until somebody presses a button, which is the
    wrong shape for "you might also look at the rules page".
    """
    ctx, client, heard = arrange(
        monkeypatch,
        [
            FakeMessage(
                [
                    call(
                        "show_view",
                        message="The whole corpus is here",
                        href="/rules",
                        label="Open rules",
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage([text("That page lists all of them.")]),
        ],
    )
    thread = thread_id()

    result = await service.run(ctx, ChatRequest(thread_id=thread, text="where are the rules?"))

    assert [c["kind"] for c in result["cards"]] == ["show_view"]
    assert heard.said == "That page lists all of them."
    # Shown, answered, and the turn ran to its end - nothing is pending.
    state = await agent.build(ctx).aget_state(service._config(ctx, thread))
    assert (state.values or {}).get("pending") == []


async def test_a_thread_id_is_namespaced_under_its_owner():
    """A guessed id lands in an empty conversation of one's own."""
    other = Principal(user_id="user_01OTHER")
    assert agent.thread_key(PRINCIPAL, "abc") != agent.thread_key(other, "abc")
    # And a traversal attempt is not a path.
    assert (
        agent.thread_key(PRINCIPAL, "../user_01OTHER:abc") == f"{PRINCIPAL.user_id}:user_01OTHERabc"
    )


# ---------------------------------------------------------------------------
# The cache prefix. These two are about the bill, not the behaviour.
# ---------------------------------------------------------------------------


def test_system_prompt_is_identical_for_two_people():
    """The prefix must not vary per person, per request or per day.

    If it does, every turn pays full price for ~2,000 tokens it should have
    read back at a tenth - and nothing visible breaks, which is why this is a
    test rather than a comment.
    """
    first = prompts.system_for(Role.renter)
    second = prompts.system_for(Role.renter)
    assert first == second
    assert first[0]["cache_control"] == {"type": "ephemeral"}
    # Exactly one breakpoint in the system half, with the tools cached behind it.
    assert sum("cache_control" in block for block in first) == 1
    # Nothing that moves may appear in it: no name, no thread id, and above
    # all not today's date. The one date it carries is the deployment's query
    # date, which is a constant.
    for role in Role:
        body = prompts.system_for(role)[0]["text"]
        assert settings.default_as_of in body
        assert (
            dt.date.today().isoformat() not in body
            or dt.date.today().isoformat() == settings.default_as_of
        )
        assert PRINCIPAL.user_id not in body


def test_tool_list_is_in_a_stable_order():
    """Tools render at position 0, so a reordering invalidates everything."""
    for role in Role:
        names = [tool["name"] for tool in tools.wire(role)]
        assert names == sorted(names)
        assert names == [tool["name"] for tool in tools.wire(role)]


def test_cache_breakpoint_moves_to_the_newest_turn():
    messages = [
        {"role": "user", "content": [text("one")]},
        {"role": "assistant", "content": [text("two")]},
        {"role": "user", "content": [text("three")]},
    ]
    agent.mark_cache(messages)
    assert messages[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    messages.append({"role": "assistant", "content": [text("four")]})
    messages.append({"role": "user", "content": [text("five")]})
    agent.mark_cache(messages)

    marked = [
        (i, j)
        for i, m in enumerate(messages)
        for j, b in enumerate(m["content"])
        if "cache_control" in b
    ]
    # One marker, on the newest turn. Four is the hard limit per request and a
    # conversation outlasts it, so the old one has to go.
    assert marked == [(4, 0)]


def test_a_cache_marker_never_lands_on_an_assistant_turn():
    """`thinking` is not a cacheable block type, and an assistant turn can end
    in one. The request always ends with a user turn, so this is belt and
    braces - but a 400 mid-conversation is expensive to debug."""
    messages = [{"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}]
    agent.mark_cache(messages)
    assert "cache_control" not in messages[0]["content"][0]


def test_marking_does_not_mutate_the_saved_transcript(monkeypatch, world):
    """The checkpointer hands back live objects; the request is a copy."""
    saved = [{"role": "user", "content": [text("hello")]}]
    copied = agent._copy(saved[0])
    agent.mark_cache([copied])
    assert "cache_control" in copied["content"][0]
    assert "cache_control" not in saved[0]["content"][0]


# ---------------------------------------------------------------------------
# Four roles, four different sets of what is even offerable
# ---------------------------------------------------------------------------


def test_an_agency_is_never_offered_an_address_of_its_own():
    """They hold none and there is no page for one, so the assistant has no
    tool that could propose it - rather than a prompt asking it not to."""
    names = {tool.name for tool in tools.roster(Role.agency)}
    assert not {"ask_to_add_building", "ask_to_pick_building", "ask_for_document"} & names
    assert "my_buildings" not in names
    assert "stock_coverage" in names


def test_the_three_who_hold_addresses_can_be_asked_for_one():
    for role in (Role.renter, Role.provider, Role.advocate):
        names = {tool.name for tool in tools.roster(role)}
        assert {"ask_to_add_building", "ask_for_document", "my_buildings"} <= names


def test_a_renter_has_no_work_queue():
    """`missing_facts` ranks a field by how many buildings it blocks. With one
    building that is not a ranking, it is a sentence."""
    assert "missing_facts" not in {tool.name for tool in tools.roster(Role.renter)}
    assert "missing_facts" in {tool.name for tool in tools.roster(Role.provider)}


def test_every_roster_tool_exists_and_every_ask_has_no_handler():
    for role in Role:
        for tool in tools.roster(role):
            assert tool.name in tools.BY_NAME
            assert tool.asks == tool.name.startswith(("ask_", "show_"))


# ---------------------------------------------------------------------------
# Context: dynamic, and therefore not in the prefix
# ---------------------------------------------------------------------------


async def test_context_is_sent_once_and_not_again(monkeypatch, world):
    ctx, client, _ = arrange(
        monkeypatch,
        [FakeMessage([text("one")]), FakeMessage([text("two")])],
    )
    thread = thread_id()

    await service.run(ctx, ChatRequest(thread_id=thread, text="first"))
    await service.run(ctx, ChatRequest(thread_id=thread, text="second"))

    def contexts(request) -> int:
        return sum(
            1
            for message in request["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "text" and block.get("text", "").startswith("<context>")
        )

    # Sent with the first turn, still in the history on the second, and not
    # repeated - it is already cached, and re-sending it is pure waste.
    assert contexts(client.requests[0]) == 1
    assert contexts(client.requests[1]) == 1


async def test_context_is_resent_when_the_buildings_change(monkeypatch, world):
    ctx, client, _ = arrange(
        monkeypatch,
        [FakeMessage([text("one")]), FakeMessage([text("two")])],
    )
    thread = thread_id()
    await service.run(ctx, ChatRequest(thread_id=thread, text="first"))

    world["places"] = [FakePlace(), FakePlace(id=8, address_id="A0404", street_address="9 Elm St")]
    await service.run(ctx, ChatRequest(thread_id=thread, text="second"))

    blocks = [
        block["text"]
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "text" and block.get("text", "").startswith("<context>")
    ]
    assert len(blocks) == 2
    assert "9 Elm St" in blocks[-1]


async def test_the_context_block_carries_what_the_prompt_may_not(monkeypatch, world):
    block = agent.context_block([FakePlace()], "Ada")
    assert "Ada" in block
    assert "A0132" in block
    # The legal city is what rules attach to, and the mailing city is only
    # worth saying because the two disagree here.
    assert "Los Angeles" in block
    # And none of it is anywhere near the system prompt.
    assert "Ada" not in prompts.system_for(Role.renter)[0]["text"]


async def test_an_agency_gets_no_building_list(monkeypatch, world):
    ctx, _, _ = arrange(monkeypatch, [FakeMessage([text("ok")])], role=Role.agency)
    block, _ = await service._context(ctx)
    assert "Saved buildings" not in block
    assert "no buildings saved" in block


# ---------------------------------------------------------------------------
# What a reload shows
# ---------------------------------------------------------------------------


def test_the_visible_transcript_is_only_what_people_said():
    messages = [
        {"role": "user", "content": [text("<context>\nAda\n</context>"), text("What applies?")]},
        {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "hm"}, call("my_buildings")],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}]},
        {"role": "assistant", "content": [text("One building.")]},
    ]
    assert service._visible(messages) == [
        {"role": "user", "text": "What applies?"},
        {"role": "assistant", "text": "One building."},
    ]


# ---------------------------------------------------------------------------
# The work queue, which is two queues
# ---------------------------------------------------------------------------


async def test_missing_facts_separates_the_two_kinds_of_blocker(monkeypatch, world):
    """An unknown naming no field is not work anybody here can do.

    At one real address 36 of 37 unknowns are a rule whose coverage clauses
    have not been translated into checkable conditions - nothing about the
    building will settle those. Counting them beside a missing year built
    makes the queue read as mostly unexplained, and makes the one field
    somebody could go and find look like a rounding error.
    """
    from app.modules.address_lookup import service as lookups
    from app.modules.assistant import tools as tool_module

    class Outcome:
        def __init__(self, result, fields):
            self.result = result
            self.unresolved_fields = fields

    class Answer:
        outcomes = [
            Outcome("unknown", ["year_built"]),
            Outcome("unknown", ["year_built", "units"]),
            # Awaiting rule review: no field named, so nothing to go and find.
            *[Outcome("unknown", []) for _ in range(9)],
            Outcome("applies", []),
        ]

    async def lookup(session, address, as_of, persist=False, include_not_applicable=False):
        return Answer()

    async def address_for(deps, address_id):
        return object()

    monkeypatch.setattr(lookups, "lookup_address", lookup)
    monkeypatch.setattr(tool_module, "_address_for", address_for)

    result = await tool_module.BY_NAME["missing_facts"].run(
        tool_module.Deps(None, PRINCIPAL, Role.provider, dt.date(2026, 10, 1)), {}
    )

    assert result["unknown_answers"] == 11
    assert result["blocked_by_a_missing_fact"] == 2
    assert result["blocked_by_rule_review"] == 9
    # Ranked by answers blocked, which is what says what to chase first.
    assert [row["field"] for row in result["blocking"]] == ["year_built", "units"]
    assert result["blocking"][0]["answers_blocked"] == 2


async def test_several_read_tools_in_one_turn_all_answer(monkeypatch, world):
    """Two calls in one assistant turn, two results, on one database session.

    Every tool reads through the request-scoped `AsyncSession`, and SQLAlchemy
    refuses two operations on a session at once - so running these with
    `asyncio.gather` answers the first and turns the rest into tool errors.
    This asserts the shape that avoids it, and that no result goes missing.
    """
    ctx, client, heard = arrange(
        monkeypatch,
        [
            FakeMessage(
                [call("my_buildings"), call("my_documents")],
                stop_reason="tool_use",
            ),
            FakeMessage([text("One building, one lease.")]),
        ],
    )

    await service.run(ctx, ChatRequest(thread_id=thread_id(), text="what do I have?"))

    results = [
        block
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(results) == 2
    assert not any(block.get("is_error") for block in results)
    # Both in one user message, as the API requires for a turn's results.
    answering = [
        message
        for message in client.requests[1]["messages"]
        if isinstance(message.get("content"), list)
        and any(b.get("type") == "tool_result" for b in message["content"])
    ]
    assert len(answering) == 1
    assert [data["name"] for data in heard.of("tool")] == ["my_buildings", "my_documents"]


# ---------------------------------------------------------------------------
# A pasted address
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        # What a browser's autofill or Google Maps hands over.
        ("415 Mission St, San Francisco, CA 94105, United States", ("Mission St", "San Francisco")),
        ("22 Precita Av, San Francisco, CA", ("Precita Av", "San Francisco")),
        ("1200 Wilshire Blvd, Los Angeles, CA 90017, USA", ("Wilshire Blvd", "Los Angeles")),
        # Nothing to relax: one part, or already a bare street.
        ("Mission St", None),
        ("San Francisco", None),
        ("", None),
    ],
)
def test_a_pasted_address_is_split_into_a_street_and_a_city(typed, expected):
    """The search matches one field at a time, so the whole paste matches none.

    The house number has to go with the rest: the sample holds 2250 and 2280
    Mission St and no 415, and an empty result cannot tell somebody "this
    street is not in the sample" from "this building is not".
    """
    assert tools.relax(typed) == expected
