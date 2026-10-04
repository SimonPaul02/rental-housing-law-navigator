"""/api/assistant - the agent behind the overview page.

One streaming route and one read. Both start from `require_principal`: the
assistant reads the caller's own buildings and documents, so there is no path
through this module that does not begin with a signature-verified token, and
the thread id is namespaced under that token's `sub` before it reaches the
transcript store.

**Why POST for a stream.** `EventSource` cannot set a header, so it cannot
carry a bearer token, and the only ways around that are a token in the query
string - logged by every proxy between here and the browser - or a cookie this
API deliberately does not have. A POST read with `fetch` keeps the token in the
`Authorization` header where it belongs, and server-sent events are just a
content type. The chat panel parses the frames itself; it is a dozen lines.
"""

from __future__ import annotations

import asyncio
import json
import logging

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.auth import Principal, require_principal
from app.core.db import get_session
from app.modules.accounts import service as accounts

from . import graph as agent
from . import prompts, service
from .schemas import ChatRequest, ThreadState

log = logging.getLogger("rhln.assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])

#: Sentinel on the queue: the turn is over, stop draining.
_DONE = object()


async def _role(session: AsyncSession, principal: Principal):
    account = await accounts.read(session, principal)
    if account is None:
        raise HTTPException(
            409,
            "This account has not picked a role yet, and the assistant is different for "
            "each of the four. Choose one and come back.",
        )
    return account.role


@router.get("/thread/{thread_id}", response_model=ThreadState)
async def read_thread(
    thread_id: str,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> ThreadState:
    """The conversation so far, so a reload does not look like a crash.

    Transcripts live in the process. A deploy ends them, which is honest for
    what this is - a conversation about a page, not a record of advice - and
    the panel opens on the greeting again rather than on a half of something.
    """
    role = await _role(session, principal)

    async def nothing(event: str, data: dict) -> None:
        return None

    ctx = agent.Session(session=session, principal=principal, role=role, emit=nothing)
    if not service.available():
        return ThreadState(
            thread_id=thread_id,
            greeting=prompts.GREETINGS[role],
            turns=[],
            cards=[],
            model=ctx.model,
            available=False,
        )
    return await service.thread(ctx, thread_id)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    principal: Principal = Depends(require_principal),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Run one turn, streaming what the agent writes and what it asks for.

    Events, in the order they can arrive: `delta` (text, many), `tool` (a
    lookup started), `card` (a control to render, which also means the turn has
    stopped), `usage` (running tokens and what they cost), `done`, `error`.
    """
    if not llm.is_configured():
        raise HTTPException(
            503,
            "ANTHROPIC_API_KEY is not set, so the assistant is unavailable. Every other "
            "page works without it.",
        )
    role = await _role(session, principal)

    # The nodes emit as they go; this drains it to the browser. A queue rather
    # than a generator because the graph drives itself and we are the ones
    # being handed events, not the ones asking for the next.
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    ctx = agent.Session(session=session, principal=principal, role=role, emit=emit)

    async def turn() -> None:
        try:
            final = await service.run(ctx, request)
            await queue.put(("done", final))
        except anthropic.APIStatusError as exc:
            await queue.put(
                (
                    "error",
                    {
                        "detail": (
                            "The model refused that request."
                            if exc.status_code == 400
                            else "The model is unavailable right now."
                        ),
                        "status": exc.status_code,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported to the browser, not raised
            log.exception("assistant turn failed")
            await queue.put(("error", {"detail": f"The turn did not complete: {exc}"}))
        finally:
            await queue.put((_DONE, {}))

    async def events():
        task = asyncio.create_task(turn())
        try:
            while True:
                event, data = await queue.get()
                if event is _DONE:
                    return
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        finally:
            # A browser that navigates away mid-turn cancels this generator.
            # The turn is then worth nothing to anybody, and its tool calls
            # cost money, so it is cancelled rather than left to finish into a
            # queue nobody is reading.
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx and Fly's proxy both buffer by default, which turns a
            # stream into one long pause followed by everything at once.
            "X-Accel-Buffering": "no",
        },
    )
