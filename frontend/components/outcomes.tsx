import type { RuleOutcome } from "@/lib/types";
import { StatusBadge } from "./ui";

/** One rule, as it reads to somebody who is not a lawyer: what it requires,
 *  then the exact words of the law it came from.
 *
 *  The quoted span is not decoration. Every record in this system was kept
 *  only because that span was found verbatim in its source document, so
 *  showing it is how a reader checks the answer rather than trusting it.
 */
export function Outcome({ outcome }: { outcome: RuleOutcome }) {
  return (
    <li
      className="rounded-xl border p-4"
      style={{ borderColor: "var(--line)", background: "var(--surface)" }}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-medium">{outcome.title ?? outcome.team_rule_id}</span>
        {outcome.status && <StatusBadge status={outcome.status} />}
        {outcome.category && (
          <span className="text-xs" style={{ color: "var(--faint)" }}>
            {outcome.category.replace(/_/g, " ")}
          </span>
        )}
      </div>

      {outcome.key_value && (
        <div className="mt-1 text-sm font-medium" style={{ color: "var(--accent)" }}>
          {outcome.key_value}
        </div>
      )}

      <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
        {outcome.explanation}
      </p>

      {outcome.unresolved_fields.length > 0 && (
        <p className="mt-2 text-sm" style={{ color: "var(--warn)" }}>
          Still unknown because the record does not say:{" "}
          {outcome.unresolved_fields.join(", ").replace(/_/g, " ")}.
        </p>
      )}

      {outcome.quoted_span && (
        <blockquote
          className="mt-3 border-l-2 pl-3 text-sm italic"
          style={{ borderColor: "var(--line)", color: "var(--faint)" }}
        >
          “{outcome.quoted_span}”
        </blockquote>
      )}

      {outcome.citation && (
        <div className="mt-2 text-xs" style={{ color: "var(--faint)" }}>
          {outcome.source_url ? (
            <a className="underline" href={outcome.source_url} rel="noopener noreferrer">
              {outcome.citation}
            </a>
          ) : (
            outcome.citation
          )}
        </div>
      )}
    </li>
  );
}

export function Outcomes({
  outcomes,
  empty,
}: {
  outcomes: RuleOutcome[];
  empty: string;
}) {
  if (outcomes.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--faint)" }}>
        {empty}
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {outcomes.map((outcome) => (
        <Outcome key={outcome.team_rule_id} outcome={outcome} />
      ))}
    </ul>
  );
}
