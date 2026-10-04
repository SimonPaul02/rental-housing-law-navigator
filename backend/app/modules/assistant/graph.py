"""The agent: two nodes, one loop, and a turn that can stop to ask.

```
                ┌───────────── read results ─────────────┐
                ▼                                        │
  message ──▶ think ──▶ tool calls? ──yes──▶ act ────────┘
                            │                 │
                            no                └── an ask among them?
                            │                           │
                            ▼                           ▼
                          END                   END, card rendered,
                      (it answered)           tool_use left unanswered
```

**think** calls the model and streams what it writes. **act** runs whatever it
asked for and loops back - except for the one case that makes this an interface
rather than a chatbot: when the model calls a tool only a *person* can
complete, `act` renders the control instead and the turn ends there with that
`tool_use` block **unanswered**. The next request carries what the person did
as its `tool_result`, so the transcript is a legal one at every point in
between - which is the only reason the pause works at all, because the API
refuses an assistant turn that follows an unanswered `tool_use`.

Why LangGraph for something this small: the loop, the pause and the resume are
all state transitions over one conversation, and a checkpointer is exactly the
thing that makes a paused turn resumable without the browser holding the
transcript. `InMemorySaver` keeps it in the process, which this deployment can
do because the backend is a persistent container rather than a function.

What the browser never sends: history. It sends one message and a thread id,
and the thread is namespaced by the token's `sub` before it reaches the saver -
so a transcript cannot be read, resumed or forged by anybody but its owner, and
a tampered id lands in its own empty thread rather than somebody else's.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

import anthropic
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.auth import Principal
from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.accounts.schemas import Role
from app.modules.rule_extraction.pipeline import models as model_specs

from . import prompts, tools
from .tools import Deps, Tool

log = logging.getLogger("rhln.assistant")

#: One shared transcript store for the process. Threads are namespaced per
#: account inside `thread_key`, so one saver is not one shared conversation.
SAVER = InMemorySaver()

#: App paths `show_view` may offer. An allowlist because the href comes from a
#: model, which is downstream of somebody's typing: without this, "link me to
#: http://…" is a working open redirect rendered as a button in our own chrome.
VIEWS = ("/home", "/places", "/rules", "/addresses", "/changes", "/settings")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _append(left: list, right: list) -> list:
    return [*left, *right]


class Turn(TypedDict, total=False):
    """One conversation, as the checkpointer keeps it.

    `messages` is the Anthropic wire format rather than LangChain's. There is
    no conversion layer on purpose: thinking blocks have to be replayed to the
    model byte-identical, `cache_control` has to land on an exact content
    block, and a round trip through a second message type is two more places
    for either to be quietly dropped.
    """

    messages: Annotated[list[dict], _append]
    #: Controls to render in the chat. Overwritten each turn, not accumulated:
    #: a card from three turns ago has already been answered or abandoned.
    cards: list[dict]
    #: `tool_use` ids waiting on a person. While this is non-empty the
    #: transcript ends mid-turn and the next request must answer every one.
    pending: list[str]
    #: Results from read tools called in the *same* assistant turn as an ask
    #: tool. They are held rather than appended because all of a turn's results
    #: have to arrive in one user message, and one of them has not happened yet.
    held: list[dict]
    #: Hash of the context block last sent, so identical context is never paid
    #: for twice. See `context_block`.
    context: str
    usage: dict
    steps: int


@dataclass(slots=True)
class Session:
    """Everything one request needs that is not part of the conversation."""

    session: AsyncSession
    principal: Principal
    role: Role
    #: Called with every event the browser should see as it happens.
    emit: Callable[[str, dict], Awaitable[None]]
    as_of: dt.date = field(default_factory=lambda: _as_of())
    model: str = field(default_factory=lambda: settings.assistant_model)

    @property
    def deps(self) -> Deps:
        return Deps(self.session, self.principal, self.role, self.as_of)


def _as_of() -> dt.date:
    try:
        return dt.date.fromisoformat(settings.default_as_of)
    except ValueError:
        return dt.date.today()


def thread_key(principal: Principal, thread_id: str) -> str:
    """Namespace a browser-supplied thread id under the account that owns it."""
    clean = "".join(c for c in thread_id if c.isalnum() or c in "-_")[:64] or "default"
    return f"{principal.user_id}:{clean}"


# ---------------------------------------------------------------------------
# Token discipline
# ---------------------------------------------------------------------------


def mark_cache(messages: list[dict]) -> None:
    """Move the conversation's cache breakpoint to the newest turn.

    Two breakpoints are in play: one is frozen on the last system block (see
    `prompts`), caching the tools and the prompt together; this is the other,
    and it has to move. Marking the end of the newest turn means the next
    request reads the entire conversation so far instead of re-processing it,
    so a long chat costs a tenth of what it looks like it should.

    The marker is cleared from wherever it was first. Only four breakpoints
    exist per request and a conversation has more turns than that, so leaving
    them behind would start failing partway into a chat. Removing one does not
    invalidate anything - what a cache entry is keyed on is the content prefix,
    and that is unchanged.
    """
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)

    if not messages:
        return
    last = messages[-1]
    # Only ever a user turn: an assistant turn can end in a `thinking` block,
    # which is not a cacheable block type, and the request always ends with
    # either the person's message or the tool results answering the model.
    if last.get("role") != "user":
        return
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
        last["content"] = content
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}


def context_block(places: list, account_name: str | None) -> str:
    """The dynamic context, as a text block for a *user* turn.

    Not the system prompt, and that is the whole point. A name or a building
    list interpolated into the prompt sits in front of the entire conversation,
    so every turn after it would re-pay for a prefix that changed - and it
    would change per person, so nothing would ever be shared. Here it sits
    after the cached history and invalidates nothing before it.
    """
    lines = []
    if account_name:
        lines.append(f"The person is {account_name}.")
    if places:
        lines.append(f"Saved buildings ({len(places)}):")
        for place in places[:12]:
            where = (
                f"{place.legal_city}, {place.legal_state or place.state}"
                if place.jurisdiction_status == "resolved"
                else f"{place.postal_city}, {place.state} (legal city unverified)"
            )
            facts = ", ".join(
                filter(
                    None,
                    [
                        f"built {place.year_built}" if place.year_built else "year built missing",
                        f"{place.units} units" if place.units else "unit count missing",
                        f"{place.contract_count} on file" if place.contract_count else None,
                    ],
                )
            )
            name = f" [{place.label}]" if place.label else ""
            lines.append(f"- {place.address_id}{name} · {place.street_address} · {where} · {facts}")
        if len(places) > 12:
            lines.append(f"- …and {len(places) - 12} more; call my_buildings for the rest.")
    else:
        lines.append("They have no buildings saved.")
    return "<context>\n" + "\n".join(lines) + "\n</context>"


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def build(ctx: Session):
    """Compile the graph for one request.

    Built per request rather than once at import because the nodes close over
    the database session and the emit callback, neither of which may be kept
    between requests - and neither of which belongs in checkpointed state,
    where it would have to be serialised.
    """
    roster = {tool.name: tool for tool in tools.roster(ctx.role)}
    wire_tools = tools.wire(ctx.role)
    system = prompts.system_for(ctx.role)

    async def think(state: Turn) -> dict:
        messages = [_copy(m) for m in state["messages"]]
        mark_cache(messages)

        try:
            async with llm.get_client().messages.stream(
                model=ctx.model,
                max_tokens=settings.assistant_max_tokens,
                system=system,
                messages=messages,
                tools=wire_tools,
                # Low effort, deliberately. This is a chat turn over results a
                # tool already computed, not a statutory reading - Module A is
                # where the careful thinking is paid for. Low effort means
                # fewer, more consolidated tool calls and no preamble, which is
                # both cheaper and closer to how the prompt asks it to write.
                #
                # `eager_input_streaming` is left off: no tool here takes an
                # input longer than an address id, so streaming partial tool
                # JSON would buy nothing and hand us a tolerant parser that can
                # return a silently truncated input.
                output_config={"effort": settings.assistant_effort},
            ) as stream:
                async for delta in stream.text_stream:
                    await ctx.emit("delta", {"text": delta})
                message = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            log.warning("assistant call failed: %s %s", exc.status_code, exc.message)
            raise

        usage = _add_usage(state.get("usage") or {}, message.usage, ctx.model)
        await ctx.emit("usage", usage)
        return {
            # `to_dict` is the SDK's own serialisation, so thinking blocks and
            # tool inputs replay to the model exactly as it produced them.
            "messages": [{"role": "assistant", "content": message.to_dict(mode="json")["content"]}],
            "usage": usage,
            "steps": (state.get("steps") or 0) + 1,
        }

    async def act(state: Turn) -> dict:
        """Run this turn's tools - and park the ones only a person can finish.

        Every `tool_use` block in the turn leaves here with exactly one
        outcome: a result, or a place in `pending`. That is not tidiness - the
        API refuses the next assistant turn unless every one of them has a
        `tool_result`, so a call that fell through a branch would make the
        whole conversation unanswerable from then on.
        """
        calls = _tool_calls(state["messages"][-1])
        readable: list[dict] = []
        askable: list[tuple[Tool, dict]] = []
        results: list[dict] = list(state.get("held") or [])

        for block in calls:
            tool = roster.get(block["name"])
            if tool is None:
                # Not in this role's roster, so the model was never given it.
                # Answered rather than dropped: an unanswered call is a 400 on
                # every subsequent turn, and "you do not have that" is
                # something it can act on.
                results.append(_error(block["id"], f"{block['name']} is not a tool you have."))
            elif tool.run is not None:
                readable.append(block)
            else:
                askable.append((tool, block))

        async def run_one(block: dict) -> dict:
            tool = roster[block["name"]]
            await ctx.emit("tool", {"name": tool.name, "label": tool.label})
            try:
                payload = await tool.run(ctx.deps, block.get("input") or {})
            except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
                log.exception("tool %s failed", tool.name)
                return _error(block["id"], f"That lookup failed: {exc}")
            return {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": tools.encode(payload),
            }

        # One at a time, and it has to be. The model may ask for the rules and
        # the change cases in one breath, and `asyncio.gather` over them looks
        # like the obvious win - but every tool here reads through the one
        # request-scoped `AsyncSession`, and SQLAlchemy refuses two operations
        # on a session at once ("This session is provisioning a new connection;
        # concurrent operations are not permitted"). The first call would
        # answer and the rest would come back as tool errors.
        #
        # Giving each call its own session would fix that and is the thing to
        # reach for if a turn ever gets slow enough to care. It is not free -
        # a pool of five would be split across concurrent turns, and these are
        # reads of a few hundred milliseconds - so the simpler shape wins until
        # something measures otherwise.
        for block in readable:
            results.append(await run_one(block))

        cards: list[dict] = []
        pending: list[str] = []
        for tool, block in askable:
            card, problem = await _card(ctx, tool, block)
            if card is None:
                # A bad ask is answered like a failed lookup rather than shown
                # to the person: the model is told why and corrects itself on
                # the next step, which is better than a broken control.
                results.append(_error(block["id"], problem or "That control could not be built."))
                continue
            cards.append(card)
            await ctx.emit("card", card)
            if not tool.waits:
                # An offer, not a question. The agent is told it was shown and
                # keeps writing; nothing waits on a click that may never come.
                results.append(_result(block["id"], "Shown to them as a card in the chat."))
                continue
            pending.append(block["id"])

        # Accumulated across the turn rather than replaced, because a card
        # shown on the first step of a loop is still on screen on the third.
        # `run` resets the list at the start of each turn, which is where a
        # card from the last question is meant to disappear.
        shown = [*(state.get("cards") or []), *cards]

        if pending:
            # The turn ends mid-air. Nothing is appended: the assistant turn's
            # `tool_use` blocks stay unanswered until the person answers them,
            # and the read results wait with them so they can all arrive in the
            # one user message the API requires.
            return {"cards": shown, "pending": pending, "held": results}
        return {
            "messages": [{"role": "user", "content": results}],
            "cards": shown,
            "held": [],
        }

    async def halt(state: Turn) -> dict:
        """End a turn that ran too long, leaving a transcript that still works.

        The step cap has to be enforced somewhere, and the obvious place -
        stopping the moment the count is reached - breaks the conversation
        permanently. A turn cut off straight after `think` ends on an assistant
        message whose `tool_use` blocks nobody answered, and a turn cut off
        straight after `act` ends on two user messages in a row. The API
        refuses both, so every later turn of that conversation is a 400.

        So the cap is checked after the results are in, and this writes the
        closing assistant turn itself. No model call: there is nothing left to
        decide, and spending one more request to say "I stopped" would be the
        opposite of a cap.
        """
        log.info("assistant hit the step cap at %s", settings.assistant_max_steps)
        said = (
            "I stopped there - that took more lookups than I allow myself in one go. "
            "Ask me again more narrowly and I will pick it up."
        )
        await ctx.emit("delta", {"text": said})
        return {"messages": [{"role": "assistant", "content": [{"type": "text", "text": said}]}]}

    def after_think(state: Turn) -> Literal["act", "__end__"]:
        # Always `act` when there are calls, even at the cap: every `tool_use`
        # has to be answered before this turn can end legally. The cap is
        # applied below, once they have been.
        return "act" if _tool_calls(state["messages"][-1]) else END

    def after_act(state: Turn) -> Literal["think", "halt", "__end__"]:
        if state.get("pending"):
            # Parked on a control. The unanswered `tool_use` is the resume
            # point, and `service` supplies its result on the next request.
            return END
        if (state.get("steps") or 0) >= settings.assistant_max_steps:
            return "halt"
        return "think"

    graph = StateGraph(Turn)
    graph.add_node("think", think)
    graph.add_node("act", act)
    graph.add_node("halt", halt)
    graph.set_entry_point("think")
    graph.add_conditional_edges("think", after_think, {"act": "act", END: END})
    graph.add_conditional_edges("act", after_act, {"think": "think", "halt": "halt", END: END})
    graph.add_edge("halt", END)
    return graph.compile(checkpointer=SAVER)


# ---------------------------------------------------------------------------
# Cards: the controls an ask tool renders.
# ---------------------------------------------------------------------------


async def _card(ctx: Session, tool: Tool, block: dict) -> tuple[dict | None, str | None]:
    """Turn an ask-tool call into a control, or say why it cannot be one.

    Every id the model supplies is checked here, against rows read through
    `accounts.service` - which filters on the token's `sub`. The model
    proposes; this decides. An ask about a building that is not the caller's
    comes back as a tool error it can correct from, never as a card: the
    alternative is a file picker pointed at somebody else's building, and the
    check cannot live in the browser.

    The options a picker needs are filled in here too, so the model never has
    to list them - which keeps them out of the transcript and off the bill.
    """
    args = block.get("input") or {}
    base = {
        "tool_use_id": block["id"],
        "kind": tool.name,
        "message": str(args.get("message") or "").strip(),
    }

    if tool.name == "ask_to_add_building":
        return {
            **base,
            "query": str(args.get("query") or "").strip()[:80],
            # Whether an address that is not on file may be kept, which is a
            # question about what the person's list is *for* rather than about
            # permission - the endpoint serves anyone. A renter has one home
            # and it is wherever they actually live, so it has to be addable.
            # The other three work from a book of buildings they are
            # answerable for; a building typed into that list would be one
            # nobody imported and nobody is answerable for. They can still ask
            # about any address - `rules_for_any_address` answers without
            # keeping anything.
            "allow_new": ctx.role is Role.renter,
        }, None

    if tool.name == "ask_for_document":
        address_id = str(args.get("address_id") or "").strip().upper()
        mine = {p.address_id: p for p in await accounts.places(ctx.session, ctx.principal)}
        place = mine.get(address_id)
        if place is None:
            return None, (
                f"{address_id or 'that address'} is not one of their saved buildings, and a "
                "document can only be filed against one that is. Call my_buildings, or "
                "ask_to_add_building first."
            )
        return {
            **base,
            "address_id": address_id,
            "place_id": place.id,
            "street": place.street_address,
        }, None

    if tool.name == "ask_to_pick_building":
        places = await accounts.places(ctx.session, ctx.principal)
        if not places:
            return None, "They have no buildings saved yet - use ask_to_add_building."
        return {
            **base,
            "options": [
                {
                    "place_id": place.id,
                    "address_id": place.address_id,
                    "label": place.label or place.street_address,
                    "where": (
                        f"{place.legal_city}, {place.legal_state or place.state}"
                        if place.jurisdiction_status == "resolved"
                        else f"{place.postal_city}, {place.state}"
                    ),
                }
                for place in places[:25]
            ],
        }, None

    if tool.name == "ask_for_agreement_details":
        try:
            document_id = int(args["document_id"])
        except (KeyError, TypeError, ValueError):
            return None, "document_id must be the integer id from my_documents."
        filed = {c.id: c for c in await accounts.contracts(ctx.session, ctx.principal)}
        document = filed.get(document_id)
        if document is None:
            return None, (
                f"Document {document_id} is not one of theirs. Call my_documents for the ids."
            )
        fields = [f for f in (args.get("fields") or []) if isinstance(f, str)]
        if not fields:
            return None, "Name at least one field to ask for."
        return {
            **base,
            "document_id": document_id,
            "filename": document.filename,
            "fields": fields,
            "current": {
                "unit_label": document.unit_label,
                "starts_on": str(document.starts_on) if document.starts_on else None,
                "ends_on": str(document.ends_on) if document.ends_on else None,
                "monthly_rent_cents": document.monthly_rent_cents,
                "note": document.note,
            },
        }, None

    if tool.name == "show_view":
        href = str(args.get("href") or "").strip()
        if not href.startswith(VIEWS) or ".." in href or "//" in href[1:] or "\\" in href:
            return None, f"href must be one of these app paths: {', '.join(VIEWS)}."
        return {**base, "href": href, "label": str(args.get("label") or "Open")[:40]}, None

    return None, f"{tool.name} has no control defined for it."


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _copy(message: dict) -> dict:
    """A deep-enough copy that moving a cache marker cannot mutate the saved
    transcript - the checkpointer hands back live objects."""
    return json.loads(json.dumps(message))


def _tool_calls(message: dict) -> list[dict]:
    if message.get("role") != "assistant":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def _result(tool_use_id: str, detail: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": detail}


def _error(tool_use_id: str, detail: str) -> dict:
    return {**_result(tool_use_id, detail), "is_error": True}


def _add_usage(total: dict, usage: Any, model: str) -> dict:
    """Running token count for this conversation, priced from the catalogue.

    Shown in the interface rather than logged, for two reasons. It is the only
    way to see whether the caching is actually working - `cached_read` staying
    at zero across turns means something in the prefix is moving - and a chat
    that spends somebody's money should say how much.
    """
    fresh = {
        "input": (total.get("input") or 0) + (usage.input_tokens or 0),
        "output": (total.get("output") or 0) + (usage.output_tokens or 0),
        "cache_write": (total.get("cache_write") or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "cache_read": (total.get("cache_read") or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0),
    }
    try:
        fresh["usd"] = model_specs.estimate_cost(
            model,
            fresh["input"] + fresh["cache_write"],
            fresh["output"],
            cached_input_tokens=fresh["cache_read"],
        )
    except ValueError:
        # An unknown model has no price here, and inventing one would be worse
        # than showing none.
        fresh["usd"] = None
    return fresh
