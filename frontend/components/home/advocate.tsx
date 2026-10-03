import Link from "next/link";
import { tryApi } from "@/lib/api";
import { Card, Distribution, Notice, SectionTitle, Stat } from "@/components/ui";
import type { ChangeTestResult, Health, RuleStats } from "@/lib/types";

/** A housing advocate's app: the evidence, and what is still contested.
 *
 * An agency wants to know how much of the stock is covered; an advocate wants
 * to know what a given answer rests on and where it could be argued. So this
 * view leads with the two things the others keep at the edge - the flagged
 * conflicts, and the change cases whose rule ids never resolved - because
 * those are where a case gets made or lost.
 */
const STATUS_COLORS: Record<string, string> = {
  in_force: "var(--status-good)",
  not_yet_effective: "var(--status-warning)",
  pending: "var(--status-serious)",
  failed: "var(--status-critical)",
};

export async function AdvocateHome() {
  const [health, rules, results] = await Promise.all([
    tryApi<Health>("/api/health"),
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
  ]);

  if (!health) {
    return (
      <Notice title="Backend unreachable" tone="warning">
        Nothing answered at <code className="mono">/api/health</code>.
      </Notice>
    );
  }

  const unresolvedCases = (results ?? []).filter((r) => !r.rules_resolved);

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">Research</h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          Every rule on record carries the span it was quoted from and the
          document that span was found in — a record whose span could not be
          located in its source was discarded rather than kept with a warning.
          Query date <strong className="mono">{health.default_as_of}</strong>.
        </p>
      </section>

      <section>
        <SectionTitle
          title="The record"
          right={
            <Link href="/rules" className="text-sm underline" style={{ color: "var(--accent)" }}>
              Open the rule library →
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Rules with a verified span" value={rules?.total ?? "—"} />
          <Stat
            label="Documents behind them"
            value={rules?.documents_extracted ?? 0}
            sub={`of ${rules?.documents_total ?? 0} in the corpus`}
          />
          <Stat
            label="Conflicts to argue"
            value={rules?.flagged_conflicts ?? 0}
            sub="two rules, one question"
            tone={rules?.flagged_conflicts ? "warning" : undefined}
          />
          <Stat
            label="Change cases run"
            value={results?.length ?? 0}
            sub={unresolvedCases.length ? `${unresolvedCases.length} unresolved` : "all resolved"}
            tone={unresolvedCases.length ? "warning" : undefined}
          />
        </div>
      </section>

      {rules && rules.total > 0 && (
        <section className="grid gap-6 lg:grid-cols-2">
          <Card>
            <SectionTitle
              title="Rules by status"
              hint="A rule not yet in force is still the answer to a question about next year."
            />
            <Distribution data={rules.by_status} colors={STATUS_COLORS} />
          </Card>
          <Card>
            <SectionTitle title="Rules by jurisdiction" />
            <Distribution data={rules.by_jurisdiction} />
          </Card>
        </section>
      )}

      <section>
        <SectionTitle
          title="Change cases"
          hint="Each answered by replaying the evaluator at the relevant dates, not by hard-coding an outcome."
          right={
            <Link href="/changes" className="text-sm underline" style={{ color: "var(--accent)" }}>
              Open change cases →
            </Link>
          }
        />
        {!results?.length ? (
          <Notice title="No change case has been run yet">
            <code className="mono">POST /api/change-tracking/run</code> runs all
            five and records which addresses move.
          </Notice>
        ) : (
          <ul className="space-y-3">
            {results.map((result) => (
              <Card key={result.test_id}>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="mono text-sm font-semibold">{result.test_id}</span>
                  <span className="font-medium">{result.title}</span>
                  <span className="ml-auto text-sm tabular-nums" style={{ color: "var(--text-muted)" }}>
                    {result.affected_address_ids.length} addresses
                  </span>
                </div>
                <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
                  {result.notes}
                </p>
                {!result.rules_resolved && (
                  <p className="mt-2 text-sm" style={{ color: "var(--status-warning)" }}>
                    Some rule ids in this case never resolved to an extracted
                    rule, so an empty set here means a gap in the record rather
                    than a finding.
                  </p>
                )}
              </Card>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
