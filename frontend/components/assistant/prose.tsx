import type { ReactNode } from "react";

/** The assistant's own words, rendered.
 *
 * A model asked for "a rule, then the two that matter" writes a bullet list
 * and bolds the rule's name, because that is what the sentence wants. Printed
 * raw, a reader gets `**Fee cap**` and asterisks around every heading, which
 * reads as a bug in the app rather than as formatting.
 *
 * So this renders the small subset that actually turns up — paragraphs,
 * bullets, bold and inline code — and nothing else. No markdown library, and
 * nothing anywhere near `dangerouslySetInnerHTML`: every piece below comes out
 * as a React text node, so a model that writes `<script>` writes four
 * characters on the screen. The text is a model's output and the citations in
 * it come from this corpus, but it has been through a tool result, which is
 * downstream of what somebody typed — that is exactly the path an injected
 * `<img onerror>` would travel, and the only safe answer is to never build
 * HTML from it.
 *
 * Anything unhandled degrades to its own source text, which is the right
 * failure: a stray `##` is ugly, and a dropped sentence is a wrong answer.
 */
export function Prose({ text }: { text: string }) {
  return <>{blocks(text)}</>;
}

function blocks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let bullets: string[] = [];

  const flush = () => {
    if (!bullets.length) return;
    const items = bullets;
    bullets = [];
    out.push(
      <ul key={`ul-${out.length}`} className="chat-list">
        {items.map((item, index) => (
          <li key={index}>{inline(item)}</li>
        ))}
      </ul>,
    );
  };

  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    const bullet = /^[-*•]\s+(.*)$/.exec(trimmed);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flush();
    if (!trimmed) continue;
    // A heading has nowhere to go in a two-sentence answer, so it is rendered
    // as the emphasised line it is meant to be rather than as an <h4> that
    // would outweigh the page's own headings.
    const heading = /^#{1,6}\s+(.*)$/.exec(trimmed);
    out.push(
      <p key={`p-${out.length}`} className={heading ? "chat-lead" : undefined}>
        {inline(heading ? heading[1] : trimmed)}
      </p>,
    );
  }
  flush();
  return out;
}

/** `**bold**` and `` `code` ``, and the text in between. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`/g;
  let at = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > at) out.push(text.slice(at, match.index));
    out.push(
      match[1] !== undefined ? (
        <strong key={match.index}>{match[1]}</strong>
      ) : (
        <code key={match.index} className="mono">
          {match[2]}
        </code>
      ),
    );
    at = match.index + match[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}
