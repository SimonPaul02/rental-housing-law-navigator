import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import { ChangesExplorer } from "@/components/explorer/changes-explorer";
import {
  Card,
  Notice,
  PageHeading,
  SectionTitle,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type {
  AddressRecord,
  CanonicalMatch,
  ChangeTest,
  ChangeTestResult,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChangesPage() {
  // Signed in is the whole check here: this page serves public corpus material,
  // which reads the same to all four roles. What differs by role is which
  // questions the dashboard puts to it, not what the record says.
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const [tests, results, canonical, addresses] = await Promise.all([
    tryApi<ChangeTest[]>("/api/change-tracking/tests"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
    tryApi<CanonicalMatch[]>("/api/change-tracking/canonical-rules"),
    tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=500"),
  ]);

  if (!tests) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  const resultsById = new Map((results ?? []).map((r) => [r.test_id, r]));
  const unmatched = (canonical ?? []).filter((c) => !c.matched);

  // Only the buildings some case actually touches cross to the browser. The
  // map needs their coordinates; it has no use for the rest of the sample, and
  // shipping all five hundred to draw a hundred would be paying for geography
  // nobody asked to see.
  const touched = new Set((results ?? []).flatMap((r) => r.affected_address_ids));
  const involved = (addresses ?? []).filter((a) => touched.has(a.address_id));

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

      <ChangesExplorer tests={tests} results={results ?? []} addresses={involved} />

      <div className="space-y-4">
        <SectionTitle
          title="Every case, side by side"
          hint="The same five, as a list — what each expects and what the evaluator found."
        />
        {tests.map((test) => {
          const result = resultsById.get(test.test_id);
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
                </div>
              ) : (
                <p className="mt-3 text-sm" style={{ color: "var(--faint)" }}>
                  Not run yet — <code className="mono">POST /api/change-tracking/tests/{test.test_id}/run</code>
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
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
