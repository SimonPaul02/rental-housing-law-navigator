import Link from "next/link";
import { tryApi } from "@/lib/api";
import { Card, Distribution, Notice, SectionTitle, Stat } from "@/components/ui";
import type { AddressStats, Health, RuleStats } from "@/lib/types";

/** A housing agency's app: coverage, not individual answers.
 *
 * The question an agency brings is not "does this rule apply at 12 Elm" but
 * "for how much of the stock can we answer at all, and where does the record
 * fail us". So the numbers that matter here are the ones the other three
 * dashboards treat as caveats: how many addresses resolved to a legal
 * jurisdiction, how often the mailing city was wrong, how many buildings have
 * no year built.
 */
interface ChangeStats {
  tests_defined: number;
  tests_run: number;
  total_affected: number;
  total_conflicts: number;
  by_test: Record<string, { affected: number; conflicts: number; as_of: string }>;
}

export async function AgencyHome() {
  const [health, addresses, rules, changes] = await Promise.all([
    tryApi<Health>("/api/health"),
    tryApi<AddressStats>("/api/address-lookup/stats"),
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<ChangeStats>("/api/change-tracking/stats"),
  ]);

  if (!health) {
    return (
      <Notice title="Backend unreachable" tone="warning">
        Nothing answered at <code className="mono">/api/health</code>. Check{" "}
        <code className="mono">fly status</code> for the API machine.
      </Notice>
    );
  }

  const unresolved = addresses?.unresolved ?? 0;

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          Jurisdiction coverage
        </h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          How much of the housing stock this system can answer for, and where
          the public record stops it. Query date{" "}
          <strong className="mono">{health.default_as_of}</strong>.
        </p>
      </section>

      <section>
        <SectionTitle
          title="The stock"
          right={
            <Link href="/addresses" className="text-sm underline" style={{ color: "var(--accent)" }}>
              Browse addresses →
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Addresses" value={addresses?.total ?? "—"} />
          <Stat
            label="Jurisdiction resolved"
            value={addresses?.resolved ?? 0}
            sub={`${unresolved} still unresolved`}
            tone={unresolved ? "warning" : "good"}
          />
          <Stat
            label="Mailing city corrected"
            value={addresses?.city_corrections ?? 0}
            sub="legal city ≠ postal city"
          />
          <Stat
            label="Missing building facts"
            value={`${addresses?.missing_year_built ?? 0} / ${addresses?.missing_units ?? 0}`}
            sub="no year built / no unit count"
            tone="warning"
          />
        </div>
        <p className="mt-3 max-w-2xl text-sm" style={{ color: "var(--text-muted)" }}>
          Those gaps are in the records, not in the software. Where coverage
          turns on a fact the data lacks, the answer is <em>unknown</em> and
          names the field — which is the part an agency can actually act on.
        </p>
      </section>

      <section>
        <SectionTitle
          title="Rule inventory"
          right={
            <Link href="/rules" className="text-sm underline" style={{ color: "var(--accent)" }}>
              Browse rules →
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Rules on record" value={rules?.total ?? "—"} />
          <Stat
            label="Source documents read"
            value={rules?.documents_extracted ?? 0}
            sub={`of ${rules?.documents_with_text ?? 0} with text`}
          />
          <Stat
            label="Conflicts flagged"
            value={rules?.flagged_conflicts ?? 0}
            sub="need a human"
            tone={rules?.flagged_conflicts ? "warning" : undefined}
          />
          <Stat
            label="Database"
            value={health.database ? "connected" : "down"}
            tone={health.database ? "good" : "critical"}
          />
        </div>
      </section>

      {rules && rules.total > 0 && (
        <section className="grid gap-6 lg:grid-cols-2">
          <Card>
            <SectionTitle title="Rules by jurisdiction" />
            <Distribution data={rules.by_jurisdiction} />
          </Card>
          <Card>
            <SectionTitle title="Rules by category" />
            <Distribution data={rules.by_category} />
          </Card>
        </section>
      )}

      <section>
        <SectionTitle
          title="Change cases"
          hint="How many buildings each supplied law change moves."
          right={
            <Link href="/changes" className="text-sm underline" style={{ color: "var(--accent)" }}>
              Open change cases →
            </Link>
          }
        />
        {!changes || changes.tests_run === 0 ? (
          <Notice title="No change case has been run yet">
            <code className="mono">POST /api/change-tracking/run</code> replays
            the evaluator at each case&rsquo;s dates and records which addresses
            move.
          </Notice>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Stat
              label="Cases run"
              value={`${changes.tests_run} / ${changes.tests_defined}`}
            />
            <Stat label="Buildings affected" value={changes.total_affected} />
            <Stat
              label="Flagged for review"
              value={changes.total_conflicts}
              tone={changes.total_conflicts ? "warning" : undefined}
            />
          </div>
        )}
      </section>
    </div>
  );
}
