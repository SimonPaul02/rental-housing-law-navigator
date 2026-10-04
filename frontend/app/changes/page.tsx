import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import {
  Card,
  Notice,
  PageHeading,
  SectionTitle,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { CanonicalMatch, ChangeTest, ChangeTestResult } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChangesPage({
  searchParams,
}: {
  searchParams: Promise<{ address_id?: string }>;
}) {
  // Signed in is the whole check here: this page serves public corpus material,
  // which reads the same to all four roles. What differs by role is which
  // questions the dashboard puts to it, not what the record says.
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const [tests, results, canonical] = await Promise.all([
    tryApi<ChangeTest[]>("/api/change-tracking/tests"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
    tryApi<CanonicalMatch[]>("/api/change-tracking/canonical-rules"),
  ]);

  if (!tests) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  const resultsById = new Map((results ?? []).map((r) => [r.test_id, r]));
  const unmatched = (canonical ?? []).filter((c) => !c.matched);
  const requestedAddress = (await searchParams).address_id?.trim().toUpperCase() ?? "";
  const selectedAddress = /^A\d{4}$/.test(requestedAddress) ? requestedAddress : "";

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Module C"
        title="Change tracking"
        lede="Each case is answered by replaying Module B's evaluator at the relevant dates — not by hard-coding the expected outcome."
      />

      {unmatched.length > 0 && (
        <Notice title="Some challenge rule ids have no extracted rule yet" tone="warning">
          {unmatched.map((c) => c.canonical_id).join(", ")} — any test that
          references these will report an empty set for an uninteresting
          reason. Run Module A extraction first.
        </Notice>
      )}

      {!results && (
        <Notice title="Current change results are unavailable" tone="warning">
          A required rule or source is missing, or a change check failed. The page
          will show results once all five cases can be replayed from current data.
        </Notice>
      )}

      <Notice title="Research aid, not legal advice">
        Check the cited law and current status before acting on a result.
      </Notice>

      <Card>
        <form action="/changes" method="get" className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            Inspect one address across the date cases
            <input
              name="address_id"
              type="text"
              pattern="A[0-9]{4}"
              defaultValue={selectedAddress}
              placeholder="A0001"
              className="mt-1 block rounded border px-3 py-2 mono"
              style={{ borderColor: "var(--line)", background: "#ffffff91" }}
            />
          </label>
          <button type="submit" className="rounded border px-4 py-2 text-sm" style={{ borderColor: "var(--line)" }}>
            Inspect
          </button>
        </form>
      </Card>

      <div className="space-y-4">
        {tests.map((test) => {
          const result = resultsById.get(test.test_id);
          const ruleSets = (result?.detail.rule_sets ?? {}) as Record<
            string,
            { before: Record<string, string>; after: Record<string, string> }
          >;
          const selectedSets = selectedAddress ? ruleSets[selectedAddress] : undefined;
          const perRule = (result?.detail.per_rule ?? {}) as Record<string, string[]>;
          const selectedRuleIds = selectedAddress
            ? Object.entries(perRule)
                .filter(([, ids]) => ids.includes(selectedAddress))
                .map(([id]) => id)
            : [];
          return (
            <Card key={test.test_id}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="mono text-sm font-semibold">{test.test_id}</span>
                <span className="font-medium">{test.title}</span>
                <span
                  className="rounded-full border px-2 py-0.5 text-xs"
                  style={{ borderColor: "var(--line)", color: "var(--faint)" }}
                >
                  {test.type}
                </span>
              </div>

              <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
                <strong>Expected:</strong> {test.expected_behavior}
              </p>

              {result ? (
                <div className="mt-3 space-y-2">
                  <p className="text-sm">{result.notes}</p>
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                    <span>
                      <strong className="tabular-nums">
                        {result.affected_address_ids.length}
                      </strong>{" "}
                      <span style={{ color: "var(--muted)" }}>affected</span>
                    </span>
                    {result.conflict_flag_address_ids.length > 0 && (
                      <span>
                        <strong className="tabular-nums">
                          {result.conflict_flag_address_ids.length}
                        </strong>{" "}
                        <span style={{ color: "var(--muted)" }}>
                          flagged for review
                        </span>
                      </span>
                    )}
                    <span className="mono" style={{ color: "var(--faint)" }}>
                      as of {result.as_of}
                    </span>
                  </div>
                  {result.detail.per_rule && (
                    <p className="text-xs" style={{ color: "var(--muted)" }}>
                      Per rule: {Object.entries(result.detail.per_rule as Record<string, string[]>)
                        .map(([id, ids]) => `${id}: ${ids.length}`)
                        .join(" · ")}
                    </p>
                  )}
                  <details className="text-xs" style={{ color: "var(--muted)" }}>
                    <summary className="cursor-pointer">Inspect affected address IDs</summary>
                    <p className="mt-2 max-h-32 overflow-y-auto mono break-words">
                      {result.affected_address_ids.join(", ") || "None"}
                    </p>
                  </details>
                  {selectedSets && (
                    <div className="mt-3 rounded border p-3 text-xs" style={{ borderColor: "var(--line)" }}>
                      <strong>{selectedAddress} · before and after rule set</strong>
                      <p className="mt-1 mono">Before: {JSON.stringify(selectedSets.before)}</p>
                      <p className="mt-1 mono">After: {JSON.stringify(selectedSets.after)}</p>
                    </div>
                  )}
                  {selectedAddress && (
                    <p className="text-xs" style={{ color: "var(--muted)" }}>
                      {selectedAddress}: {result.affected_address_ids.includes(selectedAddress)
                        ? test.type === "pending" ? "in scope if enacted" : "in affected set"
                        : "outside affected set"}
                      {selectedRuleIds.length > 0 && ` · ${selectedRuleIds.join(", ")}`}
                      {result.conflict_flag_address_ids.includes(selectedAddress) &&
                        " · possible state/local conflict for review"}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm" style={{ color: "var(--faint)" }}>
                  Current replay unavailable. Check the required sources and rule mapping.
                </p>
              )}
            </Card>
          );
        })}
      </div>

      <Card>
        <SectionTitle
          title="Rule id mapping"
          hint="How each challenge rule id resolves to our extracted records"
        />
        <Table>
          <thead>
            <tr>
              <Th>Challenge id</Th>
              <Th>Selector</Th>
              <Th>Matched</Th>
            </tr>
          </thead>
          <tbody>
            {(canonical ?? []).map((c) => (
              <tr key={c.canonical_id}>
                <Td className="mono whitespace-nowrap">{c.canonical_id}</Td>
                <Td className="text-xs">{c.selector}</Td>
                <Td className="mono text-xs">
                  {c.matched ? (
                    c.matched_rule_ids.join(", ")
                  ) : (
                    <span style={{ color: "var(--faint)" }}>none</span>
                  )}
                  {c.sources.map((source) => (
                    <div key={source.team_rule_id} className="mt-2 font-sans text-xs">
                      <a href={source.source_url} target="_blank" rel="noreferrer">
                        {source.citation}
                      </a>
                      <span> · {source.source_doc_id} · retrieved {source.retrieved_at ?? "unknown"}</span>
                      <p className="mt-1" style={{ color: "var(--muted)" }}>
                        “{source.quoted_span}”
                      </p>
                    </div>
                  ))}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
