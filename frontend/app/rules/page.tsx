import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import { RulesExplorer } from "@/components/explorer/rules-explorer";
import { anchorList } from "@/lib/jurisdictions";
import { Card, Notice, PageHeading, SectionTitle, Table, Td, Th } from "@/components/ui";
import type { AddressRecord, DocumentSummary, RuleRecord, RuleStats } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Module A's output, as the record itself.
 *
 *  The jurisdiction anchors are computed here rather than in the browser: a
 *  `Map` does not survive serialisation to a client component, and sending five
 *  hundred addresses to a page about rules would be paying for geography twice.
 *
 *  Signed in is the whole check: this page serves public corpus material, which
 *  reads the same to all four roles. What differs by role is which questions
 *  the dashboard puts to it, not what the record says.
 */
export default async function RulesPage({
  searchParams,
}: {
  searchParams: Promise<{ jurisdiction?: string; category?: string }>;
}) {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const picked = await searchParams;
  const [rules, stats, documents, addresses] = await Promise.all([
    tryApi<RuleRecord[]>("/api/rule-extraction/rules?limit=2000"),
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<DocumentSummary[]>("/api/rule-extraction/corpus/documents?has_text=true"),
    tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=500"),
  ]);

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Module A"
        title="Rule extraction"
        lede="Every record is read out of the corpus by the model, then kept only if its quoted span is found verbatim in the source document."
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
        <RulesExplorer
          rules={rules}
          stats={stats}
          anchors={anchorList(addresses ?? [])}
          initialJurisdiction={picked.jurisdiction ?? ""}
          initialCategory={picked.category ?? ""}
        />
      )}

      <Card>
        <SectionTitle
          title="Corpus"
          hint={`${documents?.length ?? 0} documents with supplied text`}
        />
        <p className="text-sm" style={{ marginBottom: 18, color: "var(--muted)" }}>
          A document with no rules is usually a correct answer rather than a gap: a bill
          status page carries no obligation to quote, and a notice ordinance, a relocation
          rate table or a broker-fee statute falls outside the six categories in scope. The
          note is what the model said about the document, so the two cases can be told apart.
        </p>
        <Table>
          <thead>
            <tr>
              <Th>Doc</Th>
              <Th>Jurisdictions</Th>
              <Th>Source</Th>
              <Th>Rules</Th>
              <Th>What the model said</Th>
            </tr>
          </thead>
          <tbody>
            {(documents ?? []).slice(0, 60).map((doc) => (
              <tr key={doc.doc_id}>
                <Td className="mono">{doc.doc_id}</Td>
                <Td className="whitespace-nowrap">{doc.jurisdictions}</Td>
                <Td className="text-xs">{doc.source_type}</Td>
                <Td className="tabular-nums">{doc.rule_count}</Td>
                <Td>
                  {doc.document_note ? (
                    <div className="text-xs" style={{ maxWidth: "46ch", color: "var(--muted)" }}>
                      {doc.document_note}
                    </div>
                  ) : (
                    <span className="text-xs" style={{ color: "var(--faint)" }}>
                      {doc.rule_count > 0 ? "no caveat recorded" : "not yet extracted"}
                    </span>
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
