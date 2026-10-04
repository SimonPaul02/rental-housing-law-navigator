import type { ReactNode } from "react";
import type { LookupResult, RuleStatus } from "@/lib/types";

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

/** The heading at the top of a page: small eyebrow, display title, one line
 *  of explanation, optional actions on the right. */
export function PageHeading({
  eyebrow,
  title,
  lede,
  actions,
}: {
  eyebrow?: string;
  title: string;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
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
  does_not_apply: { color: "var(--faint)", label: "does not apply" },
};

function Badge({ color, label }: { color: string; label: string }) {
  return (
    <span className="badge">
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
