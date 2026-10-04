/** The wire between the chat panel and `/api/assistant`.
 *
 * A card is a control the agent asked for. Each one is named after the tool
 * that produced it, and the server has already filled in and checked whatever
 * the model only proposed — a `place_id` here is one the caller owns, because
 * `graph._card` read it back through the accounts service before the card was
 * allowed to exist. The browser does not re-check; it also cannot be the thing
 * that enforces it.
 */

export type DetailField =
  | "unit_label"
  | "starts_on"
  | "ends_on"
  | "monthly_rent"
  | "note";

interface CardBase {
  tool_use_id: string;
  /** One sentence from the agent, shown above the control. */
  message: string;
}

/** Find a building this account holds, and save it. */
export interface AddBuildingCard extends CardBase {
  kind: "ask_to_add_building";
  /** What to put in the search box to start with. */
  query: string;
  /** Whether an address that is not on file may be kept anyway.
   *
   *  A renter's one home is wherever they actually live, so it has to be
   *  addable. The other three work from a book of buildings they are
   *  answerable for, and one typed into that list would be a building nobody
   *  imported. All four can still *ask* about any address — that answers
   *  without keeping anything. */
  allow_new: boolean;
}

/** File a tenancy agreement against a building they already have. */
export interface DocumentCard extends CardBase {
  kind: "ask_for_document";
  address_id: string;
  place_id: number;
  street: string;
}

/** Choose between buildings they already have. */
export interface PickBuildingCard extends CardBase {
  kind: "ask_to_pick_building";
  options: {
    place_id: number;
    address_id: string;
    label: string;
    where: string;
  }[];
}

/** Correct the term, the rent or the unit on an agreement already filed. */
export interface AgreementDetailsCard extends CardBase {
  kind: "ask_for_agreement_details";
  document_id: number;
  filename: string;
  fields: DetailField[];
  current: {
    unit_label: string | null;
    starts_on: string | null;
    ends_on: string | null;
    monthly_rent_cents: number | null;
    note: string | null;
  };
}

/** An offer, not a question — so it never holds the conversation up. */
export interface ViewCard extends CardBase {
  kind: "show_view";
  href: string;
  label: string;
}

export type Card =
  | AddBuildingCard
  | DocumentCard
  | PickBuildingCard
  | AgreementDetailsCard
  | ViewCard;

/** Which cards the conversation is actually waiting on. */
export const WAITS: Card["kind"][] = [
  "ask_to_add_building",
  "ask_for_document",
  "ask_to_pick_building",
  "ask_for_agreement_details",
];

export interface Resolution {
  tool_use_id: string;
  kind: Card["kind"];
  /** What the person did. The server re-reads it from the database before the
   *  agent is told anything, so this is a pointer, not a payload. */
  value: Record<string, unknown>;
}

export interface Turn {
  role: "user" | "assistant";
  text: string;
}

export interface ThreadState {
  thread_id: string;
  greeting: string;
  turns: Turn[];
  cards: Card[];
  usage: Usage | null;
  model: string;
  available: boolean;
}

export interface Usage {
  input: number;
  output: number;
  cache_write: number;
  cache_read: number;
  usd: number | null;
}

/** One frame of the stream. */
export type Event =
  | { event: "delta"; data: { text: string } }
  | { event: "tool"; data: { name: string; label: string } }
  | { event: "card"; data: Card }
  | { event: "usage"; data: Usage }
  | { event: "done"; data: { usage: Usage; cards: Card[] } }
  | { event: "error"; data: { detail: string; status?: number } };

/** Read a `text/event-stream` body as the frames it carries.
 *
 * Hand-rolled rather than `EventSource` for one reason: `EventSource` cannot
 * set a header, so it cannot carry the bearer token, and the alternatives are
 * a token in a query string or a cookie this API does not have. A POST read
 * with `fetch` keeps the token in `Authorization`, and the format is two
 * fields and a blank line.
 */
export async function* frames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<Event> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // A frame ends at a blank line. Anything after the last one is a partial
    // frame and stays in the buffer until the rest of it arrives.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parse(frame);
      if (parsed) yield parsed;
      split = buffer.indexOf("\n\n");
    }
  }
}

function parse(frame: string): Event | null {
  let name = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!name || data.length === 0) return null;
  try {
    return { event: name, data: JSON.parse(data.join("\n")) } as Event;
  } catch {
    return null;
  }
}

/** Tokens and what they cost, as a line a person can read.
 *
 * Shown rather than logged, and `cache_read` is in it on purpose: it is the
 * only way to see that the caching is working. A conversation several turns
 * long whose cached figure is still zero means something in the prefix is
 * moving, and the bill is several times what it should be.
 */
export function spend(usage: Usage | null): string | null {
  if (!usage) return null;
  const parts = [`${(usage.input + usage.cache_read).toLocaleString()} in`];
  if (usage.cache_read) parts.push(`${usage.cache_read.toLocaleString()} cached`);
  parts.push(`${usage.output.toLocaleString()} out`);
  if (usage.usd !== null) {
    parts.push(usage.usd < 0.01 ? "under a cent" : `$${usage.usd.toFixed(2)}`);
  }
  return parts.join(" · ");
}
