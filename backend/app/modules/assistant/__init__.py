"""The assistant: a LangGraph agent that can stop mid-turn to ask for things.

The overview page is this agent. What separates it from a chat window bolted
onto a dashboard is that when it needs something it cannot look up - a
building, a lease, a term - it renders the control that collects it, inside the
conversation, and the turn waits there.

  prompts.py   the frozen system prompt, per role: the cached prefix
  tools.py     what it can read, and what it can only ask for
  graph.py     the two-node loop, the pause, and the token accounting
  service.py   one turn: building the user message, verifying what came back
  router.py    POST /api/assistant/chat (server-sent events)
"""
