import Link from "next/link";
import { tryApi } from "@/lib/api";
import { Card, Distribution, Notice, SectionTitle, Stat } from "@/components/ui";
import type { AddressStats, Health, RuleStats } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUS_COLORS: Record<string, string> = {
  in_force: "var(--status-good)",
  not_yet_effective: "var(--status-warning)",
  pending: "var(--status-serious)",
  failed: "var(--status-critical)",
};

export default async function OverviewPage() {
  const [health, ruleStats, addressStats] = await Promise.all([
    tryApi<Health>("/api/health"),
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<AddressStats>("/api/address-lookup/stats"),
  ]);

  if (!health) {
    return (
      <Notice title="Backend unreachable" tone="warning">
        Nothing answered at <code className="mono">/api/health</code>. Start it
        with <code className="mono">make dev-api</code> locally, or check{" "}
        <code className="mono">fly status</code> for the deployed machine.
      </Notice>
    );
  }

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          Which rules apply here, on this date?
        </h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          Module A reads the supplied corpus into structured rule records.
          Module B resolves each address to its legal jurisdiction and tests
          every rule&rsquo;s coverage conditions. Module C reports which
          addresses each law-change case affects. Default query date{" "}
          <strong className="mono">{health.default_as_of}</strong>.
        </p>
      </section>

      {!health.extraction_available && (
        <Notice title="Extraction is not configured" tone="warning">
          <code className="mono">ANTHROPIC_API_KEY</code> is unset, so Module A
          returns 503. Browsing the corpus and running Modules B and C still
          work.
        </Notice>
      )}

      <section>
        <SectionTitle
          title="Corpus and extraction"
          hint="Module A"
          right={
            <Link href="/rules" className="text-sm hover:underline" style={{ color: "var(--accent)" }}>
              View rules →
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Rules extracted"
            value={ruleStats?.total ?? "—"}
            sub={`from ${ruleStats?.documents_extracted ?? 0} documents`}
          />
          <Stat
            label="Corpus documents"
            value={ruleStats?.documents_total ?? "—"}
            sub={`${ruleStats?.documents_with_text ?? 0} with supplied text`}
          />
          <Stat
            label="Conflicts flagged"
            value={ruleStats?.flagged_conflicts ?? 0}
            sub="for human review"
            tone={ruleStats?.flagged_conflicts ? "warning" : undefined}
          />
          <Stat
            label="Database"
            value={health.database ? "connected" : "down"}
            tone={health.database ? "good" : "critical"}
          />
        </div>
      </section>

      {ruleStats && ruleStats.total > 0 && (
        <section className="grid gap-6 lg:grid-cols-2">
          <Card>
            <SectionTitle title="Rules by status" />
            <Distribution data={ruleStats.by_status} colors={STATUS_COLORS} />
          </Card>
          <Card>
            <SectionTitle title="Rules by category" />
            <Distribution data={ruleStats.by_category} />
          </Card>
        </section>
      )}

      <section>
        <SectionTitle
          title="Addresses and jurisdictions"
          hint="Module B"
          right={
            <Link href="/addresses" className="text-sm hover:underline" style={{ color: "var(--accent)" }}>
              View addresses →
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Sample addresses" value={addressStats?.total ?? "—"} />
          <Stat
            label="Jurisdictions resolved"
            value={addressStats?.resolved ?? 0}
            sub={`${addressStats?.unresolved ?? 0} still unresolved`}
          />
          <Stat
            label="Mailing city corrected"
            value={addressStats?.city_corrections ?? 0}
            sub="legal city ≠ postal city"
          />
          <Stat
            label="Missing building facts"
            value={`${addressStats?.missing_year_built ?? 0} / ${addressStats?.missing_units ?? 0}`}
            sub="no year built / no unit count"
            tone="warning"
          />
        </div>
        <p className="mt-3 text-sm" style={{ color: "var(--text-muted)" }}>
          Those gaps are in the public records, not a bug. Where coverage turns
          on a fact the data lacks, the answer is <em>unknown</em> rather than a
          guess.
        </p>
      </section>
    </div>
  );
}
