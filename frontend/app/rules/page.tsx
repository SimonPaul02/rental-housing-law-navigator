import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import {
  Card,
  Notice,
  SectionTitle,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { DocumentSummary, RuleRecord, RuleStats } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function RulesPage() {
  // Who may be here is read off the role's own menu in lib/roles.ts, so this page
  // can never be reachable by a role that is not offered it. A role that may not
  // be here goes to its own dashboard: it is the wrong app, not a trespass.
  const g = await gate("/rules");
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const [rules, stats, documents] = await Promise.all([
    tryApi<RuleRecord[]>("/api/rule-extraction/rules?limit=200"),
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<DocumentSummary[]>("/api/rule-extraction/corpus/documents?has_text=true"),
  ]);

  return (
    <div className="space-y-8">
      <SectionTitle
        title="Module A · Rule extraction"
        hint="Every record is read out of the corpus by the model, then kept only if its quoted span is found verbatim in the source document."
      />

      {!rules?.length && (
        <Notice title="No rules extracted yet">
          Run a pass with{" "}
          <code className="mono">POST /api/rule-extraction/extract</code> (or{" "}
          <code className="mono">POST /api/rule-extraction/extract/&#123;doc_id&#125;</code>{" "}
          for a single document). Progress streams from{" "}
          <code className="mono">/api/rule-extraction/runs/&#123;run_id&#125;/stream</code>.
          {documents?.length
            ? ` ${documents.length} documents have supplied text and are ready to read.`
            : ""}
        </Notice>
      )}

      {!!rules?.length && (
        <>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            Showing {rules.length} of {stats?.total ?? rules.length} records.
          </p>
          <Table>
            <thead>
              <tr>
                <Th>ID</Th>
                <Th>Jurisdiction</Th>
                <Th>Category</Th>
                <Th>Status</Th>
                <Th>Requirement</Th>
                <Th>Citation</Th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.team_rule_id}>
                  <Td className="mono whitespace-nowrap">{rule.team_rule_id}</Td>
                  <Td className="whitespace-nowrap">
                    {rule.jurisdiction}
                    <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                      {rule.level}
                    </div>
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{rule.category}</Td>
                  <Td>
                    <StatusBadge status={rule.status} />
                    {rule.effective_date && (
                      <div className="mt-1 text-xs mono" style={{ color: "var(--text-muted)" }}>
                        {rule.effective_date}
                      </div>
                    )}
                  </Td>
                  <Td>
                    <div className="max-w-md">{rule.requirement}</div>
                    {rule.key_value && (
                      <div className="mt-1 text-xs font-medium">{rule.key_value}</div>
                    )}
                  </Td>
                  <Td>
                    <a
                      href={rule.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:underline"
                      style={{ color: "var(--accent)" }}
                    >
                      {rule.citation}
                    </a>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}

      <Card>
        <SectionTitle
          title="Corpus"
          hint={`${documents?.length ?? 0} documents with supplied text`}
        />
        <Table>
          <thead>
            <tr>
              <Th>Doc</Th>
              <Th>Jurisdictions</Th>
              <Th>Source</Th>
              <Th>Rules</Th>
            </tr>
          </thead>
          <tbody>
            {(documents ?? []).slice(0, 60).map((doc) => (
              <tr key={doc.doc_id}>
                <Td className="mono">{doc.doc_id}</Td>
                <Td className="whitespace-nowrap">{doc.jurisdictions}</Td>
                <Td className="text-xs">{doc.source_type}</Td>
                <Td className="tabular-nums">{doc.rule_count}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
