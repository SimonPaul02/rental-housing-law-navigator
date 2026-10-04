import type { ReactNode } from "react";
import type { Confidence, JurisdictionStatus, LookupResult, RuleStatus } from "@/lib/types";

/* ------------------------------------------------------------------ shell */
export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`card ${className}`}>{children}</div>;
}

/** The heading at the top of a page: display title, one line of explanation,
 *  optional actions on the right. */
export function PageHeading({
  title,
  lede,
  actions,
}: {
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        {lede && <p>{lede}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </header>
  );
}

export function SectionTitle({
  title,
  hint,
  right,
}: {
  title: string;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <div className="section-title">
      <div>
        <h2>{title}</h2>
        {hint && <p>{hint}</p>}
      </div>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------- stat strip */
export function StatStrip({ children }: { children: ReactNode }) {
  return <div className="stats-strip">{children}</div>;
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "good" | "warning" | "critical";
}) {
  const color =
    tone === "good"
      ? "var(--good)"
      : tone === "warning"
        ? "var(--warn)"
        : tone === "critical"
          ? "var(--critical)"
          : "var(--ink)";
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color }}>
        {value}
      </div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------- badges */
/* Colour never carries meaning alone — every badge states its label. */

const RULE_STATUS: Record<RuleStatus, { color: string; label: string }> = {
  in_force: { color: "var(--good)", label: "in force" },
  not_yet_effective: { color: "var(--warn)", label: "not yet effective" },
  pending: { color: "var(--serious)", label: "pending" },
  failed: { color: "var(--critical)", label: "failed" },
};

const RESULT: Record<LookupResult, { color: string; label: string }> = {
  applies: { color: "var(--good)", label: "applies" },
  unknown: { color: "var(--warn)", label: "unknown" },
  // Covered, but another rule governs this same obligation here.
  superseded: { color: "var(--accent)", label: "superseded" },
  not_yet_effective: { color: "var(--accent)", label: "not yet effective" },
  pending: { color: "var(--accent)", label: "pending" },
  // Internal only - never written to lookups.json.
  does_not_apply: { color: "var(--faint)", label: "does not apply" },
};

const JURISDICTION_STATUS: Record<JurisdictionStatus, { color: string; label: string }> = {
  resolved: { color: "var(--good)", label: "Jurisdiction resolved" },
  needs_review: { color: "var(--warn)", label: "Jurisdiction needs review" },
  not_checked: { color: "var(--faint)", label: "Jurisdiction not checked" },
};

function Badge({ color, label, title }: { color: string; label: string; title?: string }) {
  return (
    <span className="badge" title={title}>
      <span className="badge-dot" style={{ background: color }} aria-hidden />
      {label}
    </span>
  );
}

export function StatusBadge({ status }: { status: RuleStatus }) {
  const s = RULE_STATUS[status] ?? { color: "var(--faint)", label: status };
  return <Badge color={s.color} label={s.label} />;
}

export function ResultBadge({ result }: { result: LookupResult }) {
  const r = RESULT[result] ?? { color: "var(--faint)", label: result };
  return <Badge color={r.color} label={r.label} />;
}

export function ConfidenceBadge({
  confidence,
  reasons = [],
}: {
  confidence: Confidence;
  reasons?: string[];
}) {
  return confidence === "low" ? (
    <Badge
      color="var(--warn)"
      label="low confidence"
      title={reasons.length ? reasons.join("\n") : "Rests on the machine's own reading"}
    />
  ) : (
    <Badge
      color="var(--good)"
      label="source-backed"
      title="Every check behind this answer is the cited source or a named reviewer"
    />
  );
}

export function JurisdictionBadge({ status }: { status: JurisdictionStatus }) {
  const item = JURISDICTION_STATUS[status];
  return <Badge color={item.color} label={item.label} />;
}

export function ZipDiscrepancyBadge() {
  return <Badge color="var(--serious)" label="ZIP differs" />;
}

export function RuleCheckBadge({ unknownCount }: { unknownCount: number }) {
  return unknownCount > 0
    ? <Badge color="var(--warn)" label={`${unknownCount} rule checks pending`} />
    : <Badge color="var(--good)" label="No rule checks pending" />;
}

/* ------------------------------------------------------------------ table */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="table-wrap">
      <div className="table-scroll">
        <table className="data">{children}</table>
      </div>
    </div>
  );
}

export function Th({ children }: { children: ReactNode }) {
  return <th>{children}</th>;
}

export function Td({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <td className={className}>{children}</td>;
}

/* ----------------------------------------------------------------- notice */
export function Notice({
  title,
  children,
  tone = "info",
}: {
  title: string;
  children?: ReactNode;
  tone?: "info" | "warning" | "accent";
}) {
  return (
    <div className={`notice${tone === "info" ? "" : ` ${tone}`}`}>
      <div>
        <h3>{title}</h3>
        {children && <p>{children}</p>}
      </div>
    </div>
  );
}

/** One categorical dimension as labelled bars. Labels are always present, so
 *  it never depends on colour to be read. */
export function Distribution({
  data,
  colors,
}: {
  data: Record<string, number>;
  colors?: Record<string, string>;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (!total) {
    return (
      <p style={{ fontSize: 13, color: "var(--faint)" }}>Nothing to show yet.</p>
    );
  }
  return (
    <ul style={{ display: "grid", gap: 10, listStyle: "none", padding: 0, margin: 0 }}>
      {entries.map(([key, n]) => (
        <li key={key} className="bar-row">
          <span
            style={{
              width: 170,
              flex: "none",
              fontSize: 13,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {key}
          </span>
          <span className="bar-track">
            <span
              className="bar-fill"
              style={{
                width: `${(n / total) * 100}%`,
                background: colors?.[key] ?? "var(--accent)",
              }}
            />
          </span>
          <span
            style={{
              width: 44,
              flex: "none",
              textAlign: "right",
              fontSize: 13,
              color: "var(--muted)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {n}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------- every field, shown */
/** One record's full contents, as label/value pairs.
 *
 *  A rule carries twenty fields and the summary row can show six, so the rest
 *  need somewhere to live. This renders inside a `<details>` so the table stays
 *  scannable by default and still holds the whole record - no client component,
 *  no state, and it works with JavaScript switched off.
 *
 *  An absent value is shown as a dash rather than hidden: "this rule states no
 *  exemptions" and "we never asked about exemptions" are different facts, and a
 *  reviewer checking coverage needs to tell them apart.
 */
export function FieldList({
  fields,
}: {
  fields: [string, ReactNode][];
}) {
  return (
    <dl className="fieldlist">
      {fields.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value === null || value === undefined || value === "" ? "—" : value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A disclosure that matches the table's type scale. */
export function Expand({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <details className="expand">
      <summary>{label}</summary>
      <div>{children}</div>
    </details>
  );
}
