import Link from "next/link";
import { tryApi } from "@/lib/api";
import {
  Card,
  Distribution,
  JurisdictionBadge,
  Notice,
  RuleCheckBadge,
  SectionTitle,
  Stat,
  StatStrip,
  ZipDiscrepancyBadge,
} from "@/components/ui";
import { Outcomes } from "@/components/outcomes";
import type {
  AddressStats,
  ChangeTestResult,
  Health,
  LookupResponse,
  Place,
  RuleStats,
} from "@/lib/types";

/** The dashboard, the same one for all four roles.
 *
 * It has two halves, and they are the two halves the tailored versions will be
 * made of: what the law says about *your* buildings, and what the record holds
 * overall. A renter will eventually get the first half only and in plainer
 * words; an agency the second half with coverage broken out. Keeping both in
 * one page until then means the tailoring is a matter of choosing and
 * rewording, not of building the parts.
 */

/** How many saved buildings get evaluated. Each is an independent request, so
 *  the cap is about the page's own latency, not about the API. */
const MAX_EVALUATED = 4;

interface ChangeStats {
  tests_defined: number;
  tests_run: number;
  total_affected: number;
  total_conflicts: number;
}

const STATUS_COLORS: Record<string, string> = {
  in_force: "var(--good)",
  not_yet_effective: "var(--warn)",
  pending: "var(--serious)",
  failed: "var(--critical)",
};

export async function Dashboard({
  places,
  roleLabel,
}: {
  places: Place[];
  roleLabel: string;
}) {
  const [health, ruleStats, addressStats, changeStats, changeResults] =
    await Promise.all([
      tryApi<Health>("/api/health"),
      tryApi<RuleStats>("/api/rule-extraction/stats"),
      tryApi<AddressStats>("/api/address-lookup/stats"),
      tryApi<ChangeStats>("/api/change-tracking/stats"),
      tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
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

  const evaluated = places.slice(0, MAX_EVALUATED);
  const lookups = await Promise.all(
    evaluated.map((place) =>
      tryApi<LookupResponse>(`/api/address-lookup/lookup/${place.address_id}`),
    ),
  );

  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          Which rules apply here, on this date?
        </h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--muted)" }}
        >
          You signed up as a <strong>{roleLabel.toLowerCase()}</strong>. Every
          role sees this same page for now; the four tailored views come later.
          Default query date{" "}
          <strong className="mono">{health.default_as_of}</strong>.
        </p>
      </section>

      {!health.extraction_available && (
        <Notice title="Extraction is not configured" tone="warning">
          <code className="mono">ANTHROPIC_API_KEY</code> is unset, so rule
          extraction returns 503. Browsing the corpus and evaluating addresses
          still work.
        </Notice>
      )}

      {/* ---------------------------------------------- your own buildings */}
      <section className="space-y-4">
        <SectionTitle
          title="Your addresses"
          hint="Private to you. Each rule below quotes the law it came from, so you can check it rather than take our word for it."
          right={
            <Link
              href="/places"
              className="text-sm hover:underline"
              style={{ color: "var(--accent)" }}
            >
              {places.length ? "Edit addresses →" : "Add an address →"}
            </Link>
          }
        />

        {places.length === 0 ? (
          <Notice title="No address saved yet">
            Save a building and this section fills in: which rules cover it on
            the query date, what each one requires, and which answers are still
            blocked by a fact the public record does not carry.
          </Notice>
        ) : (
          <>
            {places.length > MAX_EVALUATED && (
              <p className="text-sm" style={{ color: "var(--faint)" }}>
                Evaluating the first {MAX_EVALUATED} of {places.length}.
              </p>
            )}
            {evaluated.map((place, index) => (
              <Building
                key={place.id}
                place={place}
                lookup={lookups[index]}
                rulesLoaded={Boolean(ruleStats?.total)}
              />
            ))}
          </>
        )}
      </section>

      {/* ------------------------------------------------------ the record */}
      <section>
        <SectionTitle
          title="The record"
          hint="What the system has read in, and how far it reaches."
        />
        <StatStrip>
          <Stat
            label="Rules on record"
            value={ruleStats?.total ?? "—"}
            sub={`from ${ruleStats?.documents_extracted ?? 0} of ${
              ruleStats?.documents_with_text ?? 0
            } documents`}
          />
          <Stat
            label="Sample addresses"
            value={addressStats?.total ?? "—"}
            sub={`${addressStats?.resolved ?? 0} with a legal jurisdiction`}
          />
          <Stat
            label="Mailing city corrected"
            value={addressStats?.city_corrections ?? 0}
            sub="legal city ≠ postal city"
          />
          <Stat
            label="Conflicts flagged"
            value={ruleStats?.flagged_conflicts ?? 0}
            sub="need a human"
            tone={ruleStats?.flagged_conflicts ? "warning" : undefined}
          />
        </StatStrip>
        <p className="mt-3 max-w-2xl text-sm" style={{ color: "var(--faint)" }}>
          {addressStats?.missing_year_built ?? 0} buildings have no year built
          and {addressStats?.missing_units ?? 0} no unit count. Those gaps are in
          the public records, not a bug: where coverage turns on a fact the data
          lacks, the answer is <em>unknown</em> and names the field.
        </p>
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

      {/* ----------------------------------------------------- law changes */}
      <section>
        <SectionTitle
          title="Law changes"
          hint="Each case is answered by replaying the evaluator at the relevant dates — not by hard-coding the expected outcome."
          right={
            <Link
              href="/changes"
              className="text-sm hover:underline"
              style={{ color: "var(--accent)" }}
            >
              Open change cases →
            </Link>
          }
        />
        {!changeStats || changeStats.tests_run === 0 ? (
          <Notice title="No change case has been run yet">
            <code className="mono">POST /api/change-tracking/run</code> runs all
            of them and records which addresses move.
          </Notice>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Stat
                label="Cases run"
                value={`${changeStats.tests_run} / ${changeStats.tests_defined}`}
              />
              <Stat label="Buildings affected" value={changeStats.total_affected} />
              <Stat
                label="Flagged for review"
                value={changeStats.total_conflicts}
                tone={changeStats.total_conflicts ? "warning" : undefined}
              />
            </div>
            <ChangesForYou places={places} results={changeResults ?? []} />
          </>
        )}
      </section>
    </div>
  );
}

/** One saved building: where it legally is, and what the law says about it. */
function Building({
  place,
  lookup,
  rulesLoaded,
}: {
  place: Place;
  lookup: LookupResponse | null;
  rulesLoaded: boolean;
}) {
  const applies = lookup?.outcomes.filter((o) => o.result === "applies") ?? [];
  const unknown = lookup?.outcomes.filter((o) => o.result === "unknown") ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div>
            <div className="text-lg font-medium">
              {place.label ?? place.street_address}
            </div>
            <div className="text-sm" style={{ color: "var(--muted)" }}>
              {place.jurisdiction_status === "resolved"
                ? `${place.legal_city}, ${place.legal_state ?? place.state}`
                : `${place.postal_city}, ${place.state} (mailing city; legal city pending)`}
              {place.postal_city_differs && (
                <>
                  {" — your mail says "}
                  <strong>{place.postal_city}</strong>, but the rules that cover
                  this building are {place.legal_city}&rsquo;s.
                </>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <JurisdictionBadge status={place.jurisdiction_status} />
              {place.zip_discrepancy && <ZipDiscrepancyBadge />}
              {lookup && rulesLoaded && <RuleCheckBadge unknownCount={unknown.length} />}
            </div>
          </div>
          <div className="text-sm tabular-nums" style={{ color: "var(--faint)" }}>
            {place.year_built ? `built ${place.year_built}` : "year built unknown"}
            {" · "}
            {place.units ? `${place.units} units` : "unit count unknown"}
          </div>
        </div>
      </Card>

      {!lookup ? (
        <Notice title="No evaluation for this building yet" tone="warning">
          The rule set has not been read in yet, or the API could not answer.
          Nothing is wrong with the address.
        </Notice>
      ) : (
        <>
          {place.jurisdiction_status !== "resolved" && (
            <Notice title="Legal city pending review" tone="warning">
              City-specific rule answers remain uncertain until the legal city is verified.
            </Notice>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Stat
              label="Rules that apply"
              value={applies.length}
              tone={applies.length ? "good" : undefined}
            />
            <Stat
              label="Blocked by a missing fact"
              value={unknown.length}
              sub="not a guess either way"
              tone={unknown.length ? "warning" : undefined}
            />
          </div>

          <Outcomes
            outcomes={applies}
            empty="No rule in the corpus covers this building on this date."
          />

          {unknown.length > 0 && (
            <details>
              <summary
                className="cursor-pointer text-sm"
                style={{ color: "var(--muted)" }}
              >
                {unknown.length} cannot be answered yet — each names the fact
                that would settle it
              </summary>
              <div className="mt-3">
                <Outcomes outcomes={unknown} empty="" />
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

/** The change cases that touch a building this person actually saved. */
function ChangesForYou({
  places,
  results,
}: {
  places: Place[];
  results: ChangeTestResult[];
}) {
  const saved = new Set(places.map((p) => p.address_id));
  if (saved.size === 0) return null;

  const mine = results.filter((result) =>
    result.affected_address_ids.some((id) => saved.has(id)),
  );
  if (mine.length === 0) {
    return (
      <p className="mt-4 text-sm" style={{ color: "var(--faint)" }}>
        None of these cases moves an answer at your {places.length === 1 ? "address" : "addresses"}.
      </p>
    );
  }

  return (
    <ul className="mt-4 space-y-3">
      {mine.map((result) => (
        <Card key={result.test_id}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="mono text-sm font-semibold">{result.test_id}</span>
            <span className="font-medium">{result.title}</span>
            <span
              className="rounded-full border px-2 py-0.5 text-xs"
              style={{ borderColor: "var(--line)", color: "var(--faint)" }}
            >
              affects you
            </span>
          </div>
          <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
            {result.notes}
          </p>
        </Card>
      ))}
    </ul>
  );
}
