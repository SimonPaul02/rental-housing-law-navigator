import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import { ChangesExplorer } from "@/components/explorer/changes-explorer";
import { Card, Expand, Notice, PageHeading, Table, Td, Th } from "@/components/ui";
import type {
  AddressRecord,
  CanonicalMatch,
  ChangeTest,
  ChangeTestResult,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/** Module C, as a page a person can read.
 *
 *  Everything a case has to say is said inside the case — its dates, its
 *  numbers, how to read them, the buildings it moves and the law it read. This
 *  file is therefore short on purpose: it fetches, states the page's one
 *  caveat, and hands over.
 *
 *  Two things used to live here and no longer do. There was a second rendering
 *  of all five cases under the explorer, so every fact about T3 appeared twice
 *  on one page and the two could disagree. And there was a text box wanting an
 *  address id typed in `A0001` form, whose answer then appeared scattered
 *  through that duplicate list; picking a building on the map or in the table
 *  says the same thing in the place a reader is already looking. The
 *  `?address_id=` link still works and now arrives as a pre-selected building.
 */
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

  const [tests, results, canonical, addresses] = await Promise.all([
    tryApi<ChangeTest[]>("/api/change-tracking/tests"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
    tryApi<CanonicalMatch[]>("/api/change-tracking/canonical-rules"),
    tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=500"),
  ]);

  if (!tests) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  const unmatched = (canonical ?? []).filter((c) => !c.matched);
  const requested = (await searchParams).address_id?.trim().toUpperCase() ?? "";
  const linkedAddress = /^A\d{4}$/.test(requested) ? requested : "";

  // Only the buildings some case actually touches cross to the browser. The
  // map needs their coordinates; it has no use for the rest of the book, and
  // shipping all five hundred to draw a hundred would be paying for geography
  // nobody asked to see.
  const touched = new Set((results ?? []).flatMap((r) => r.affected_address_ids));
  const involved = (addresses ?? []).filter((a) => touched.has(a.address_id));

  return (
    <div className="space-y-8">
      <PageHeading
        title="Change tracking"
        lede="Which rules change, when, and for which buildings. Every answer here is produced by re-running the ordinary address lookup on the dates each case names — never by writing down an expected outcome in advance."
      />

      <Notice title="Research aid, not legal advice">
        Check the cited law and its current status before acting on anything on
        this page.
      </Notice>

      {!results && (
        <Notice title="The replay could not be reached" tone="warning">
          The cases below are the definitions only. Their answers come from a
          separate call that did not return.
        </Notice>
      )}

      {/* One root cause behind every blocked case, named once with its fix.
          Each case still says for itself which id it was waiting on. */}
      {unmatched.length > 0 && (
        <Notice title="Some rules in these cases have not been extracted yet" tone="warning">
          No verified record matches {unmatched.map((c) => c.canonical_id).join(", ")}.
          A case that needs one of them reports that it cannot be answered,
          rather than reporting that it affects nobody — except the failed
          measure, whose empty answer rests on a rent-cap check that runs
          without its record, so it is answered and marked partly answered.
          Running an extraction pass over the corpus is what fills them in.
        </Notice>
      )}

      <ChangesExplorer
        tests={tests}
        results={results ?? []}
        addresses={involved}
        initialAddressId={linkedAddress}
      />

      {/* The id scheme itself, for a reader auditing the bridge rather than
          reading a case. Each case already carries the law it read. */}
      <Card>
        <Expand label="How the brief's rule ids map onto our records">
          <p className="case-note">
            The cases name rules by the challenge&apos;s ids; our extracted
            records carry ours. The bridge is an explicit table rather than a
            fuzzy match, so an id with nothing behind it is visible here
            instead of quietly producing an empty answer.
          </p>
          <Table>
            <thead>
              <tr>
                <Th>Id in the brief</Th>
                <Th>What we look for</Th>
                <Th>What it found</Th>
              </tr>
            </thead>
            <tbody>
              {(canonical ?? []).map((c) => (
                <tr key={c.canonical_id}>
                  <Td className="mono whitespace-nowrap">{c.canonical_id}</Td>
                  <Td className="text-xs">{c.selector}</Td>
                  <Td className="text-xs">
                    {c.matched ? (
                      <span className="mono">{c.matched_rule_ids.join(", ")}</span>
                    ) : (
                      <span style={{ color: "var(--faint)" }}>nothing yet</span>
                    )}
                    {c.note && <p className="case-note">{c.note}</p>}
                    {/* The citation only. The span of text each rule rests on
                        is shown inside the case that reads it, where somebody
                        is actually weighing it. */}
                    {c.sources.map((source) => (
                      <p key={source.team_rule_id} className="case-note">
                        <a href={source.source_url} target="_blank" rel="noreferrer">
                          {source.citation}
                        </a>
                        {source.source_doc_id && <span> · {source.source_doc_id}</span>}
                        <span> · retrieved {source.retrieved_at ?? "unknown"}</span>
                      </p>
                    ))}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Expand>
      </Card>
    </div>
  );
}
