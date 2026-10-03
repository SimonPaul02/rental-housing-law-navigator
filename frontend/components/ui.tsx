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
  return (
    <div
      className={`rounded-xl border p-5 ${className}`}
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      {children}
    </div>
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
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {hint && (
          <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            {hint}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------- stat tiles */
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
      ? "var(--status-good)"
      : tone === "warning"
        ? "var(--status-warning)"
        : tone === "critical"
          ? "var(--status-critical)"
          : "var(--text-primary)";
  return (
    <Card>
      <div
        className="text-xs font-medium uppercase tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </div>
      <div className="mt-2 text-3xl font-semibold tabular-nums" style={{ color }}>
        {value}
      </div>
      {sub && (
        <div className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {sub}
        </div>
      )}
    </Card>
  );
}

/* ----------------------------------------------------------------- badges */
/* Status colour is never the only signal - every badge carries its label. */

const RULE_STATUS: Record<RuleStatus, { color: string; label: string }> = {
  in_force: { color: "var(--status-good)", label: "in force" },
  not_yet_effective: { color: "var(--status-warning)", label: "not yet effective" },
  pending: { color: "var(--status-serious)", label: "pending" },
  failed: { color: "var(--status-critical)", label: "failed" },
};

const RESULT: Record<LookupResult, { color: string; label: string }> = {
  applies: { color: "var(--status-good)", label: "applies" },
  unknown: { color: "var(--status-warning)", label: "unknown" },
  does_not_apply: { color: "var(--text-muted)", label: "does not apply" },
};

function Badge({ color, label }: { color: string; label: string }) {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
    >
      <span
        aria-hidden
        className="h-2 w-2 rounded-full"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

export function StatusBadge({ status }: { status: RuleStatus }) {
  const s = RULE_STATUS[status] ?? { color: "var(--text-muted)", label: status };
  return <Badge color={s.color} label={s.label} />;
}

export function ResultBadge({ result }: { result: LookupResult }) {
  const r = RESULT[result] ?? { color: "var(--text-muted)", label: result };
  return <Badge color={r.color} label={r.label} />;
}

/* ------------------------------------------------------------------ table */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div
      className="overflow-x-auto rounded-xl border"
      style={{ borderColor: "var(--border)" }}
    >
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children }: { children: ReactNode }) {
  return (
    <th
      className="border-b px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide"
      style={{
        borderColor: "var(--border)",
        background: "var(--surface-2)",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <td
      className={`border-b px-3 py-2 align-top ${className}`}
      style={{ borderColor: "var(--border)" }}
    >
      {children}
    </td>
  );
}

/* ------------------------------------------------------------- empty/error */
export function Notice({
  title,
  children,
  tone = "info",
}: {
  title: string;
  children?: ReactNode;
  tone?: "info" | "warning";
}) {
  return (
    <Card className={tone === "warning" ? "border-l-4" : ""}>
      <div className="flex items-start gap-3">
        {tone === "warning" && (
          <span
            aria-hidden
            className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
            style={{ background: "var(--status-warning)" }}
          />
        )}
        <div>
          <div className="font-medium">{title}</div>
          {children && (
            <div
              className="mt-1 text-sm leading-relaxed"
              style={{ color: "var(--text-secondary)" }}
            >
              {children}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

/** Horizontal distribution bar. One categorical dimension, labels always
 *  present, so it never relies on colour alone. */
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
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        Nothing to show yet.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {entries.map(([key, n]) => (
        <li key={key} className="flex items-center gap-3">
          <span className="w-44 shrink-0 truncate text-sm">{key}</span>
          <span
            className="h-2 flex-1 overflow-hidden rounded-full"
            style={{ background: "var(--surface-2)" }}
          >
            <span
              className="block h-full rounded-full"
              style={{
                width: `${(n / total) * 100}%`,
                background: colors?.[key] ?? "var(--accent)",
              }}
            />
          </span>
          <span
            className="w-12 shrink-0 text-right text-sm tabular-nums"
            style={{ color: "var(--text-secondary)" }}
          >
            {n}
          </span>
        </li>
      ))}
    </ul>
  );
}
