"""The wire between the chat panel and the agent.

Deliberately thin in one direction: a request carries one message and a thread
id, never history. The transcript lives in the checkpointer, keyed by the
token's `sub`, so there is nothing for a browser to replay, truncate or forge -
and a turn that stopped to ask a question resumes from the server's own record
of what it asked.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Resolution(BaseModel):
    """What the person did with a control the assistant rendered.

    `summary` is the sentence the model is told, and the server writes it from
    what actually happened rather than taking the browser's word: the card
    handlers in `service.py` do the work themselves - save the building, file
    the document - and describe their own outcome.
    """

    tool_use_id: str = Field(max_length=128)
    #: Which control, so the server knows which action to perform.
    kind: str = Field(max_length=64)
    #: The person's input, shape depending on `kind`.
    value: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    #: Per browser tab. Namespaced under the account before it reaches the
    #: saver, so an invented id opens an empty conversation of one's own.
    thread_id: str = Field(max_length=64)
    #: What they typed, if they typed anything. A card can be answered in
    #: silence, and a question can arrive while a card is still open.
    text: str | None = Field(default=None, max_length=4000)
    #: Answers to controls still waiting. Every pending id must be accounted
    #: for; the ones left out are reported to the model as declined.
    resolutions: list[Resolution] = Field(default_factory=list)


class ThreadState(BaseModel):
    """What the panel needs to redraw itself after a reload."""

    thread_id: str
    greeting: str
    #: Only what a person said or was shown: tool traffic is not transcript.
    turns: list[dict]
    cards: list[dict]
    usage: dict | None = None
    model: str
    available: bool
