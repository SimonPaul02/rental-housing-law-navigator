import Link from "next/link";
import {
  Card,
  Distribution,
  JurisdictionBadge,
  Notice,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { AddressExplorer } from "@/components/explorer/address-explorer";
import { tryApi } from "@/lib/api";
import { ROLES } from "@/lib/roles";
import type {
  AddressRecord,
  AddressStats,
  ChangeStats,
  ChangeTestResult,
  Health,
  Place,
  RuleCompilation,
  RuleStats,
  ZipReviewCase,
} from "@/lib/types";
import { ManageLink } from "./shared";

/** The agency's app: coverage, and where the record fails.
 *
 * An agency has no address of its own. Their subject is the whole stock —
 * every one of the 500 sample rows, whether or not anybody saved it — so this
 * is the one dashboard that is complete before the person has done anything,
 * and the one where a saved address is a footnote rather than the point.
 *
 * It is also the only view whose headline number is a *denominator*. The
 * question an agency brings is not "what applies here" but "how much of the
 * stock can this system answer for, and what is stopping the rest" — so the
 * gaps are the content, not a caveat under it.
 */

interface DefectRow {
  what: string;
  count: number;
  of: number;
  why: string;
  href?: string;
}

export async function AgencyHome({ places, health }: { places: Place[]; health: Health }) {
  const [addresses, addressStats, ruleStats, changeStats, changeResults, zipCases, compilation] =
    await Promise.all([
      tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=500"),
      tryApi<AddressStats>("/api/address-lookup/stats"),
      tryApi<RuleStats>("/api/rule-extraction/stats"),
      tryApi<ChangeStats>("/api/change-tracking/stats"),
      tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
      tryApi<ZipReviewCase[]>("/api/address-lookup/zip-review"),
      tryApi<RuleCompilation>("/api/address-lookup/rule-compilation"),
    ]);

  if (!addressStats) {
    return (
      <div className="space-y-8">
        <Intro health={health} />
        <Notice title="No address data" tone="warning">
          <code className="mono">/api/address-lookup/stats</code> did not answer.
          Run <code className="mono">make seed</code> to load the sample.
        </Notice>
      </div>
    );
  }

  const share = addressStats.total
    ? Math.round((addressStats.resolved / addressStats.total) * 100)
    : 0;

  const defects: DefectRow[] = [
    {
      what: "No verified legal jurisdiction",
      count: addressStats.unresolved,
      of: addressStats.total,
      why: "City rules cannot be settled. The mailing city is never substituted for a verified legal city, so these stay unanswered rather than wrong.",
      href: "/addresses?status=pending",
    },
    {
      what: "No year built",
      count: addressStats.missing_year_built,
      of: addressStats.total,
      why: "Any rule with an age or cutoff-year condition answers unknown and names the field.",
    },
    {
      what: "No unit count",
      count: addressStats.missing_units,
      of: addressStats.total,
      why: "Small-landlord exemptions and unit thresholds cannot be tested either way.",
    },
    {
      what: "ZIP disagrees with the geocoder",
      count: zipCases?.length ?? 0,
      of: addressStats.total,
      why: "Tracked separately because it does not change the legal city. A finding here needs a source, not a guess.",
    },
    {
      what: "Rule conflicts flagged",
      count: ruleStats?.flagged_conflicts ?? 0,
      of: ruleStats?.total ?? 0,
      why: "Two rules reaching the same obligation with no stated precedence. These need a human.",
      href: "/rules",
    },
  ];

  const unreviewed = compilation?.summary.needs_review ?? 0;
  const untranslated = compilation?.summary.with_unmapped_text ?? 0;
  const ruleQueue = (compilation?.queue ?? []).filter((item) => item.team_rule_id).slice(0, 12);

  return (
    <div className="space-y-10">
      <Intro health={health} />

      <StatStrip>
        <Stat
          label="Addresses in the sample"
          value={addressStats.total}
          sub="the whole stock this system holds"
        />
        <Stat
          label="Legal jurisdiction verified"
          value={`${share}%`}
          sub={`${addressStats.resolved} of ${addressStats.total}`}
          tone={share > 90 ? "good" : share > 60 ? "warning" : "critical"}
        />
        <Stat
          label="Mailing city corrected"
          value={addressStats.city_corrections}
          sub="legal city ≠ postal city"
        />
        <Stat
          label="Rules on record"
          value={ruleStats?.total ?? "—"}
          sub={`from ${ruleStats?.documents_extracted ?? 0} of ${
            ruleStats?.documents_with_text ?? 0
          } documents`}
        />
      </StatStrip>

      {/* --------------------------------------------------- the whole stock */}
      <section>
        <AddressExplorer
          addresses={addresses ?? []}
          stats={addressStats}
          canSave
          heading="The stock, address by address"
          hint="Every row in the sample. Filter it, highlight a jurisdiction to see where it sits relative to the rest, and read the same selection as a table — which is also the version that prints and that a screen reader can follow."
        />
      </section>

      {/* ----------------------------------------------- where it fails */}
      <section>
        <SectionTitle
          title="Where the record fails"
          hint="Each line is a reason an answer cannot be given for part of the stock. They are reported separately because they have different remedies and different owners."
        />
        <Table>
          <thead>
            <tr>
              <Th>Defect</Th>
              <Th>Rows</Th>
              <Th>Share</Th>
              <Th>What it costs</Th>
            </tr>
          </thead>
          <tbody>
            {defects.map((row) => (
              <tr key={row.what}>
                <Td className="font-medium">
                  {row.href ? (
                    <Link href={row.href} className="underline" style={{ color: "var(--accent)" }}>
                      {row.what}
                    </Link>
                  ) : (
                    row.what
                  )}
                </Td>
                <Td className="tabular-nums">{row.count}</Td>
                <Td className="tabular-nums">
                  <span style={{ color: "var(--muted)" }}>
                    {row.of ? `${Math.round((row.count / row.of) * 100)}%` : "—"}
                  </span>
                </Td>
                <Td>
                  <div style={{ maxWidth: "58ch" }}>{row.why}</div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </section>

      {/* --------------------------------------------- how it was resolved */}
      <section className="grid gap-6 lg:grid-cols-2">
        <Card>
          <SectionTitle
            title="How each jurisdiction was established"
            hint="Only verified methods count towards coverage."
          />
          <Distribution data={addressStats.by_method} />
        </Card>
        <Card>
          <SectionTitle title="The stock by state" />
          <Distribution data={addressStats.by_state} />
        </Card>
      </section>

      {/* ------------------------------------------------------ ZIP review */}
      <section>
        <SectionTitle
          title="ZIP findings awaiting a source"
          hint="A ZIP that disagrees with every accepted geocoder endpoint is recorded, not corrected. Overriding one takes a cited source, which is why these sit in a queue rather than being fixed automatically."
        />
        {!zipCases?.length ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            No ZIP discrepancies in the current assessment.
          </p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Address</Th>
                <Th>Legal city</Th>
                <Th>Supplied ZIP</Th>
                <Th>Geocoder said</Th>
                <Th>Assessment</Th>
                <Th>Human finding</Th>
              </tr>
            </thead>
            <tbody>
              {zipCases.slice(0, 15).map((row) => (
                <tr key={row.address_id}>
                  <Td>
                    <div>{row.street_address}</div>
                    <div className="text-xs mono" style={{ color: "var(--faint)" }}>
                      {row.address_id} · {row.postal_city}, {row.state}
                    </div>
                  </Td>
                  <Td>{row.legal_city ?? "—"}</Td>
                  <Td className="mono">{row.assessment.input_zip || "—"}</Td>
                  <Td className="mono">{row.assessment.matched_zips.join(", ") || "—"}</Td>
                  <Td className="text-xs">{row.assessment.status.replace(/_/g, " ")}</Td>
                  <Td className="text-xs">
                    {row.review ? (
                      <a
                        className="underline"
                        href={row.review.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--accent)" }}
                      >
                        {row.review.decision}
                        {row.review.confirmed_zip ? ` → ${row.review.confirmed_zip}` : ""}
                      </a>
                    ) : (
                      <span style={{ color: "var(--faint)" }}>none yet</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {zipCases && zipCases.length > 15 && (
          <p className="mt-2 text-sm" style={{ color: "var(--faint)" }}>
            Showing 15 of {zipCases.length}.
          </p>
        )}
      </section>

      {/* ----------------------------------------- the rule set's own gaps */}
      <section>
        <SectionTitle
          title="Rules the evaluator cannot yet act on"
          hint="A coverage condition that was not translated into a predicate answers unknown for every address it could reach. This queue is ordered by how much untranslated text each rule still carries, because that is how much it is costing."
        />
        {!compilation ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            <code className="mono">/api/address-lookup/rule-compilation</code> did
            not answer.
          </p>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Stat
                label="Rules compiled"
                value={compilation.summary.rules}
                sub="translated into predicates"
              />
              <Stat
                label="Awaiting review"
                value={unreviewed}
                tone={unreviewed ? "warning" : "good"}
                sub="not yet approved by a human"
              />
              <Stat
                label="With untranslated text"
                value={untranslated}
                tone={untranslated ? "warning" : "good"}
                sub="answers unknown wherever they reach"
              />
            </div>
            {ruleQueue.length > 0 && (
              <div className="mt-5">
                <Table>
                  <thead>
                    <tr>
                      <Th>Rule</Th>
                      <Th>Jurisdiction</Th>
                      <Th>Obligation</Th>
                      <Th>Untranslated</Th>
                      <Th>Review state</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {ruleQueue.map((item) => (
                      <tr key={item.team_rule_id}>
                        <Td className="mono">{item.team_rule_id}</Td>
                        <Td>{item.jurisdiction ?? "—"}</Td>
                        <Td className="text-xs">{item.issue_key ?? "—"}</Td>
                        <Td className="tabular-nums">{item.unmapped_count ?? 0}</Td>
                        <Td className="text-xs">{item.review_state ?? "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
          </>
        )}
      </section>

      {/* ----------------------------------------------------- the changes */}
      <section>
        <SectionTitle
          title="How much each change case moves"
          hint="Answered by replaying the evaluator at the relevant dates across the whole sample, not by hard-coding an expected count."
          right={<ManageLink href="/changes" label="Open change cases" />}
        />
        {!changeStats || changeStats.tests_run === 0 ? (
          <Notice title="No change case has been run yet">
            <code className="mono">POST /api/change-tracking/run</code> runs all
            of them and records which addresses move.
          </Notice>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Case</Th>
                <Th>Buildings moved</Th>
                <Th>Share of stock</Th>
                <Th>Flagged for review</Th>
                <Th>As of</Th>
              </tr>
            </thead>
            <tbody>
              {(changeResults ?? []).map((result) => (
                <tr key={result.test_id}>
                  <Td>
                    <span className="mono text-xs">{result.test_id}</span>{" "}
                    <span className="font-medium">{result.title}</span>
                    <div className="mt-1 text-xs" style={{ maxWidth: "56ch", color: "var(--faint)" }}>
                      {result.notes}
                    </div>
                  </Td>
                  <Td className="tabular-nums">{result.affected_address_ids.length}</Td>
                  <Td className="tabular-nums">
                    <span style={{ color: "var(--muted)" }}>
                      {addressStats.total
                        ? `${Math.round(
                            (result.affected_address_ids.length / addressStats.total) * 100,
                          )}%`
                        : "—"}
                    </span>
                  </Td>
                  <Td className="tabular-nums">
                    {result.conflict_flag_address_ids.length || (
                      <span style={{ color: "var(--faint)" }}>—</span>
                    )}
                  </Td>
                  <Td className="mono text-xs">{result.as_of}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>

      {/* ------------------------------------------------------ spot checks */}
      <section className="space-y-3">
        <SectionTitle
          title="Your spot checks"
          hint="A shortlist, not a caseload — nothing above waits for it."
          right={<ManageLink label="Edit spot checks" />}
        />
        {places.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Nothing pinned. Pin an address when you want to watch one particular
            record rather than the aggregate.
          </p>
        ) : (
          <ul className="space-y-2">
            {places.map((place) => (
              <li
                key={place.id}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border p-3 text-sm"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <span className="min-w-48 flex-1">
                  <span className="font-medium">{place.street_address}</span>
                  <span className="ml-2" style={{ color: "var(--muted)" }}>
                    {place.legal_city ?? place.postal_city}, {place.state}
                  </span>
                  {place.label && (
                    <span className="ml-2" style={{ color: "var(--faint)" }}>
                      — {place.label}
                    </span>
                  )}
                </span>
                <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                  {place.address_id}
                </span>
                <JurisdictionBadge status={place.jurisdiction_status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Intro({ health }: { health: Health }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">
        How much of the stock can be answered
      </h1>
      <p className="mt-2 max-w-2xl leading-relaxed" style={{ color: "var(--muted)" }}>
        {ROLES.agency.cardinalityNote} Coverage is measured as of{" "}
        <strong className="mono">{health.default_as_of}</strong>.
      </p>
    </section>
  );
}
