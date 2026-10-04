"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { accessToken } from "@/lib/session";
import type { Role } from "@/lib/roles";
import { ROLES } from "@/lib/roles";
import { Spinner } from "../google-mark";
import { CardView } from "./cards";
import { Prose } from "./prose";
import {
  type Card,
  type Resolution,
  type ThreadState,
  type Turn,
  type Usage,
  frames,
  spend,
  WAITS,
} from "./model";

/** The overview page's primary interface.
 *
 * What the agent can read it reads by itself. What it cannot — a building, a
 * lease, a term — it asks for by rendering the app's own control inside the
 * conversation, and the turn waits there until somebody uses it. That is the
 * whole design: the answer and the thing blocking the answer are in the same
 * place, so nobody is sent to a different page to unblock a sentence.
 *
 * The transcript lives on the server, keyed by the account. The browser sends
 * one message and a thread id and never replays history — which is why a
 * reload redraws the conversation and why nothing here can forge a turn.
 */

const SUGGESTIONS: Record<Role, string[]> = {
  renter: [
    "What applies to my home right now?",
    "Can my rent be increased this year?",
    "What is still unsettled about my building?",
  ],
  provider: [
    "What binds all of my buildings?",
    "Which missing fact is blocking the most answers?",
    "Which exemptions can I actually claim?",
  ],
  agency: [
    "How much of the sample can be answered?",
    "What is stopping the rest?",
    "Where is the mailing city not the legal city?",
  ],
  advocate: [
    "Which rules reach my newest case?",
    "Give me the quoted span and the citation",
    "Where does the record conflict with itself?",
  ],
};

export function Assistant({ role }: { role: Role }) {
  const [thread] = useState(threadId);
  const [state, setState] = useState<ThreadState | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [writing, setWriting] = useState("");
  const [doing, setDoing] = useState<string | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const log = useRef<HTMLDivElement>(null);

  // Load whatever this thread already said. A reload is not a new
  // conversation, and an empty panel after one looks like a crash.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const loaded = await get<ThreadState>(`/assistant/thread/${thread}`);
        if (!live) return;
        setState(loaded);
        setTurns(loaded.turns);
        setCards(loaded.cards);
        setUsage(loaded.usage);
      } catch (failure) {
        if (live) {
          setError(
            failure instanceof Error ? failure.message : "The assistant did not answer.",
          );
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [thread]);

  // Follow the newest line while something is being written.
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [turns, writing, cards, doing]);

  const send = useCallback(
    async (text: string | null, resolutions: Resolution[] = []) => {
      setBusy(true);
      setError("");
      setWriting("");
      setDoing(null);
      if (text) setTurns((current) => [...current, { role: "user", text }]);
      // A control that has been used is spent, whatever the agent does next.
      if (resolutions.length) {
        const answered = new Set(resolutions.map((r) => r.tool_use_id));
        setCards((current) => current.filter((c) => !answered.has(c.tool_use_id)));
      }

      let said = "";
      try {
        const response = await fetch("/api/assistant/chat", {
          method: "POST",
          headers: await headers(),
          body: JSON.stringify({ thread_id: thread, text, resolutions }),
        });
        if (!response.ok || !response.body) {
          const body = await response.json().catch(() => ({}));
          throw new Error(
            typeof body.detail === "string" ? body.detail : "The assistant did not answer.",
          );
        }
        for await (const frame of frames(response.body)) {
          if (frame.event === "delta") {
            said += frame.data.text;
            setWriting(said);
          } else if (frame.event === "tool") {
            setDoing(frame.data.label);
          } else if (frame.event === "card") {
            setCards((current) => [...current, frame.data]);
          } else if (frame.event === "usage") {
            setUsage(frame.data);
          } else if (frame.event === "error") {
            setError(frame.data.detail);
          }
        }
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "The assistant did not answer.");
      }
      if (said.trim()) setTurns((current) => [...current, { role: "assistant", text: said }]);
      setWriting("");
      setDoing(null);
      setBusy(false);
    },
    [thread],
  );

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    await send(text);
  }

  if (state && !state.available) return <Unavailable />;

  const waiting = cards.filter((card) => WAITS.includes(card.kind));
  const empty = turns.length === 0 && !writing && !busy;

  return (
    <section className="chat" aria-label="Assistant">
      <header className="chat-head">
        <div>
          <p className="eyebrow">Ask</p>
          <h2>{ROLES[role].label}</h2>
        </div>
        {usage && (
          <p
            className="chat-spend"
            title="Tokens this conversation has used. The cached figure is the prefix read back instead of re-sent."
          >
            {spend(usage)}
          </p>
        )}
      </header>

      <div className="chat-log" ref={log} aria-live="polite" aria-busy={busy}>
        {state && empty && (
          <>
            <p className="chat-greeting">{state.greeting}</p>
            <ul className="chat-suggestions">
              {SUGGESTIONS[role].map((suggestion) => (
                <li key={suggestion}>
                  <button
                    type="button"
                    className="chat-suggestion"
                    onClick={() => send(suggestion)}
                    disabled={busy}
                  >
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        {!state && !error && (
          <p className="chat-greeting">
            <Spinner /> Opening the conversation…
          </p>
        )}

        {turns.map((turn, index) => (
          <Bubble key={`${turn.role}-${index}`} turn={turn} />
        ))}
        {writing && <Bubble turn={{ role: "assistant", text: writing }} />}
        {doing && (
          <p className="chat-doing">
            <Spinner /> {doing}…
          </p>
        )}
        {busy && !writing && !doing && (
          <p className="chat-doing">
            <Spinner /> Thinking…
          </p>
        )}

        {cards.map((card) => (
          <CardView
            key={card.tool_use_id}
            card={card}
            busy={busy}
            onDone={(resolution) => send(null, [resolution])}
          />
        ))}

        {error && (
          <p role="alert" className="chat-card-error">
            {error}
          </p>
        )}
      </div>

      <form onSubmit={submit} className="chat-composer">
        <input
          className="field"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={
            waiting.length
              ? "Use the form above, or say something else"
              : "Ask about an address, a rule, or a change"
          }
          aria-label="Ask the assistant"
          disabled={busy}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
          {busy ? <Spinner /> : null}
          Ask
        </button>
      </form>

      <p className="chat-footnote">
        Every answer comes from this deployment&rsquo;s own corpus and quotes
        the text it was read from. Not legal advice.
      </p>
    </section>
  );
}

function Bubble({ turn }: { turn: Turn }) {
  return (
    <div className={`chat-turn ${turn.role}`}>
      <div className="chat-bubble">
        <Prose text={turn.text} />
      </div>
    </div>
  );
}

function Unavailable() {
  return (
    <section className="chat" aria-label="Assistant">
      <header className="chat-head">
        <div>
          <p className="eyebrow">Ask</p>
          <h2>The assistant is not configured</h2>
        </div>
      </header>
      <div className="chat-log">
        <p className="chat-greeting">
          <code className="mono">ANTHROPIC_API_KEY</code> is not set on the API,
          so there is nothing to ask. Everything below this panel is unaffected
          — it is read out of the database and needs no model.
        </p>
      </div>
    </section>
  );
}

/** One thread per browser tab, remembered across reloads of that tab.
 *
 * Per tab rather than per account because two tabs are two conversations, and
 * `sessionStorage` is what expresses that. The server namespaces whatever
 * arrives under the token's `sub`, so an id guessed here reaches an empty
 * conversation of one's own rather than somebody else's.
 */
function threadId(): string {
  const key = "rhln.assistant.thread";
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const fresh = crypto.randomUUID();
    sessionStorage.setItem(key, fresh);
    return fresh;
  } catch {
    // Private mode, or storage switched off. A conversation that lasts the
    // life of the component is still a conversation.
    return crypto.randomUUID();
  }
}

async function headers(): Promise<HeadersInit> {
  const token = await accessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, { headers: await headers() });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "That did not answer.");
  }
  return (await response.json()) as T;
}
