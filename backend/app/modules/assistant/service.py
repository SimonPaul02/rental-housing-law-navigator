"""Driving one turn, and closing the loop on a control the person used.

The agent asks for things by rendering controls, and the browser completes
them against the endpoints that already exist - `POST /accounts/me/places`
saves a building, the multipart contracts route files a document. That is
deliberate: there is exactly one write path for a building and one for a
document in this API, both of them already authorised and validated, and a
second one reachable through a chat message would be a second place for the
ownership check to be forgotten.

So what a resolution carries is not a result, it is a claim. Every one of them
is re-read from the database here, through `accounts.service`, which filters on
the token's `sub` - and the sentence the model is told is written from what the
row actually says. The model is never told what the browser said happened.
"""

from __future__ import annotations

import hashlib
import logging

from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.accounts.schemas import Role

from . import graph as agent
from . import prompts
from .schemas import ChatRequest, Resolution, ThreadState

log = logging.getLogger("rhln.assistant")


async def thread(ctx: agent.Session, thread_id: str) -> ThreadState:
    """What the panel draws on load: the transcript, and any open control."""
    compiled = agent.build(ctx)
    snapshot = await compiled.aget_state(_config(ctx, thread_id))
    values = snapshot.values or {}
    return ThreadState(
        thread_id=thread_id,
        greeting=prompts.GREETINGS[ctx.role],
        turns=_visible(values.get("messages") or []),
        cards=values.get("cards") or [],
        usage=values.get("usage"),
        model=ctx.model,
        available=available(),
    )


async def run(ctx: agent.Session, request: ChatRequest) -> dict:
    """One turn, from the person's message to wherever the agent stops."""
    compiled = agent.build(ctx)
    config = _config(ctx, request.thread_id)
    snapshot = await compiled.aget_state(config)
    values = snapshot.values or {}

    fresh, digest = await _context_if_changed(ctx, values)
    content = _opening(request, fresh, await _answers(ctx, request, values))
    if not content:
        return {"usage": values.get("usage") or {}, "cards": values.get("cards") or []}

    final = await compiled.ainvoke(
        {
            "messages": [{"role": "user", "content": content}],
            # Each of these is per-turn and must not survive the last one: a
            # card already answered, an id no longer pending, a step count from
            # a different question.
            "cards": [],
            "pending": [],
            "held": [],
            "steps": 0,
            "context": digest,
        },
        config,
    )
    return {"usage": final.get("usage") or {}, "cards": final.get("cards") or []}


# ---------------------------------------------------------------------------
# Building the user turn
# ---------------------------------------------------------------------------


async def _answers(ctx: agent.Session, request: ChatRequest, values: dict) -> list[dict]:
    """The `tool_result` blocks this turn owes the model, if any.

    A turn that stopped to ask left `tool_use` blocks unanswered, and the API
    refuses the next assistant turn until every one of them has a result. The
    read results held back from that same turn belong here too, so that all of
    its results arrive in the one user message.
    """
    pending: list[str] = list(values.get("pending") or [])
    if not pending:
        return []
    blocks: list[dict] = list(values.get("held") or [])
    answered = {r.tool_use_id: r for r in request.resolutions}
    for tool_use_id in pending:
        blocks.append(await _answer(ctx, tool_use_id, answered.get(tool_use_id)))
    return blocks


def _opening(request: ChatRequest, context: str | None, answers: list[dict]) -> list[dict]:
    """The content blocks that open this turn.

    Order is fixed by the API and reads the right way round anyway: every
    `tool_result` first, because a turn answering an assistant's tool calls has
    to begin with them; then the context; then what the person said.
    """
    blocks: list[dict] = list(answers)
    if context:
        blocks.append({"type": "text", "text": context})
    text = (request.text or "").strip()
    if text:
        blocks.append({"type": "text", "text": text})
    elif not answers:
        # Nothing said and nothing owed: not a turn. A context block on its own
        # is not a question, and sending one would make the agent answer a
        # question nobody asked.
        return []
    return blocks


async def _answer(ctx: agent.Session, tool_use_id: str, resolution: Resolution | None) -> dict:
    """The `tool_result` for one control, written from verified state."""
    if resolution is None:
        return _result(
            tool_use_id,
            "The person did not use that control. Do not ask for the same thing again - "
            "carry on with what you can answer without it, or say what is blocked.",
        )
    try:
        detail = await _verify(ctx, resolution)
    except Exception as exc:  # noqa: BLE001 - told to the model, not raised
        log.exception("resolving %s failed", resolution.kind)
        return _result(tool_use_id, f"That did not go through: {exc}", error=True)
    return _result(tool_use_id, detail)


async def _verify(ctx: agent.Session, resolution: Resolution) -> str:
    """Re-read what the browser says happened, and describe what is true."""
    value = resolution.value or {}

    if resolution.kind in ("ask_to_add_building", "ask_to_pick_building"):
        address_id = str(value.get("address_id") or "").strip().upper()
        mine = {p.address_id: p for p in await accounts.places(ctx.session, ctx.principal)}
        place = mine.get(address_id)
        if place is None:
            return (
                f"No building {address_id or '(none given)'} is saved to this account. "
                "Nothing was added."
            )
        added = resolution.kind == "ask_to_add_building"
        verb = "is now saved" if added else "is the one they mean"
        where = (
            f"{place.legal_city}, {place.legal_state or place.state}"
            if place.jurisdiction_status == "resolved"
            else f"{place.postal_city}, {place.state} — legal city unverified"
        )
        facts = ", ".join(
            [
                f"built {place.year_built}" if place.year_built else "year built not in the record",
                f"{place.units} units" if place.units else "unit count not in the record",
            ]
        )
        return f"{address_id} {verb}: {place.street_address}, {where} ({facts})."

    if resolution.kind in ("ask_for_document", "ask_for_agreement_details"):
        try:
            document_id = int(value.get("document_id"))
        except (TypeError, ValueError):
            return "Nothing was filed."
        filed = {c.id: c for c in await accounts.contracts(ctx.session, ctx.principal)}
        document = filed.get(document_id)
        if document is None:
            return f"No document {document_id} belongs to this account. Nothing was filed."
        places = {p.id: p for p in await accounts.places(ctx.session, ctx.principal)}
        where = getattr(places.get(document.place_id), "address_id", "an unknown building")
        parts = [f"{document.filename} ({document.kind}) is on file against {where}"]
        if document.unit_label:
            parts.append(f"unit {document.unit_label}")
        if document.starts_on or document.ends_on:
            parts.append(f"term {document.starts_on or '?'} to {document.ends_on or '?'}")
        if document.monthly_rent_cents is not None:
            # Self-reported, unverified, and not to be multiplied by anything.
            parts.append(
                f"rent recorded as ${document.monthly_rent_cents / 100:,.2f} a month "
                "(their own figure, not read from the file)"
            )
        return (
            ", ".join(parts)
            + ". The file itself has not been read and nothing in it has been verified."
        )

    return "That control returned nothing usable."


def _result(tool_use_id: str, content: str, *, error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if error:
        block["is_error"] = True
    return block


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


async def _context(ctx: agent.Session) -> tuple[str, str]:
    """The dynamic context block, and a digest of it.

    An agency is given no building list because they hold no buildings - the
    whole sample is their subject - so for them this is their name and nothing
    else.
    """
    account = await accounts.read(ctx.session, ctx.principal)
    places = [] if ctx.role is Role.agency else await accounts.places(ctx.session, ctx.principal)
    text = agent.context_block(places, getattr(account, "name", None))
    return text, hashlib.sha256(text.encode()).hexdigest()[:16]


async def _context_if_changed(ctx: agent.Session, values: dict) -> tuple[str | None, str]:
    """The block to send, or `None` when the model has already been told.

    Re-sending an identical context block every turn is the kind of waste that
    shows up nowhere except the bill: it is already in the transcript, and the
    transcript is already cached. Two small local queries to find that out is a
    trade worth making - and because the digest is of the rendered text, a
    building saved on another page is picked up on the next turn without
    anything having to notify us.
    """
    text, digest = await _context(ctx)
    return (None if values.get("context") == digest else text), digest


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _visible(messages: list[dict]) -> list[dict]:
    """The transcript as a person would recognise it.

    Tool traffic is not transcript. What is kept is what somebody typed and
    what the assistant wrote back - which is also all the panel can render, and
    all it should: a reload is not an invitation to re-read the machinery.
    """
    turns: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts = [content] if content.strip() else []
        elif isinstance(content, list):
            parts = [
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                # The injected context block is ours, not theirs.
                and not block["text"].startswith("<context>")
            ]
        else:
            parts = []
        text = "\n\n".join(p.strip() for p in parts if p.strip())
        if text:
            turns.append({"role": message.get("role", "assistant"), "text": text})
    return turns


def _config(ctx: agent.Session, thread_id: str) -> dict:
    return {"configurable": {"thread_id": agent.thread_key(ctx.principal, thread_id)}}


def available() -> bool:
    """Whether a chat can happen at all.

    The same guard Module A uses: with no key the panel says so plainly and
    every other page keeps working, rather than the app half-failing.
    """
    return bool(settings.anthropic_api_key)
