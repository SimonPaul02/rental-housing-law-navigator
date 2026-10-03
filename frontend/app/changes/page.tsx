import { tryApi } from "@/lib/api";
import { Card, Notice, SectionTitle, Table, Td, Th } from "@/components/ui";
import type { CanonicalMatch, ChangeTest, ChangeTestResult } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ChangesPage() {
  const [tests, results, canonical] = await Promise.all([
    tryApi<ChangeTest[]>("/api/c/tests"),
    tryApi<ChangeTestResult[]>("/api/c/results"),
    tryApi<CanonicalMatch[]>("/api/c/canonical-rules"),
  ]);

  if (!tests) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  const resultsById = new Map((results ?? []).map((r) => [r.test_id, r]));
  const unmatched = (canonical ?? []).filter((c) => !c.matched);

  return (
    <div className="space-y-8">
      <SectionTitle
        title="Module C · Change tracking"
        hint="Each case is answered by replaying Module B's evaluator at the relevant dates — not by hard-coding the expected outcome."
      />

      {unmatched.length > 0 && (
        <Notice title="Some challenge rule ids have no extracted rule yet" tone="warning">
          {unmatched.map((c) => c.canonical_id).join(", ")} — any test that
          references these will report an empty set for an uninteresting
          reason. Run Module A extraction first.
        </Notice>
      )}

      <div className="space-y-4">
        {tests.map((test) => {
          const result = resultsById.get(test.test_id);
          return (
            <Card key={test.test_id}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="mono text-sm font-semibold">{test.test_id}</span>
                <span className="font-medium">{test.title}</span>
                <span
                  className="rounded-full border px-2 py-0.5 text-xs"
                  style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
                >
                  {test.type}
                </span>
              </div>

              <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
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
                      <span style={{ color: "var(--text-secondary)" }}>affected</span>
                    </span>
                    {result.conflict_flag_address_ids.length > 0 && (
                      <span>
                        <strong className="tabular-nums">
                          {result.conflict_flag_address_ids.length}
                        </strong>{" "}
                        <span style={{ color: "var(--text-secondary)" }}>
                          flagged for review
                        </span>
                      </span>
                    )}
                    <span className="mono" style={{ color: "var(--text-muted)" }}>
                      as of {result.as_of}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="mt-3 text-sm" style={{ color: "var(--text-muted)" }}>
                  Not run yet — <code className="mono">POST /api/c/tests/{test.test_id}/run</code>
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
                    <span style={{ color: "var(--text-muted)" }}>none</span>
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
