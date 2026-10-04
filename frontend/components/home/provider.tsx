import Link from "next/link";
import {
  Card,
  Notice,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { MapPanel } from "@/components/map/map-panel";
import { tryApi } from "@/lib/api";
import { placePoint, unplaced } from "@/lib/points";
import { categoryName, fieldName } from "@/lib/lookups";
import { PLACES, ROLES } from "@/lib/roles";
import type {
  ChangeTestResult,
  Health,
  Place,
  Portfolio,
  PortfolioBuilding,
  PortfolioRule,
  RuleStats,
} from "@/lib/types";
import { Facts, ManageLink, NoRules, WhereItIs } from "./shared";

/** The provider's app: a portfolio, answered together.
 *
 * A housing provider is answerable for every building at once, so the page is
 * the roll-up and a single building is the drill-down — the exact inverse of
 * the renter's. Three things follow from that, and they are the three sections
 * nobody else gets:
 *
 * 1. A matrix, because "which of my buildings is subject to a rent cap" is a
 *    question about a column, not about any one building.
 * 2. A missing-fact queue ordered by how many answers each field would
 *    unblock, because that is the only ordering that says what to go and find
 *    first.
 * 3. The rules that were tested and still do not bind — compliance needs the
 *    negatives as much as the positives.
 *
 * **The arithmetic is not done here any more, and that is the whole difference
 * between a page that renders and one that does not.** An account is set up
 * holding the buildings it is answerable for, which is the whole imported book
 * rather than the handful somebody types in, and this page used to fetch every
 * outcome for every one of them: 500 buildings against 115 rules is 57,500
 * outcomes carrying their checks, their explanations and their quoted spans —
 * 75 MB, ten seconds, and nothing ever painted. `/accounts/me/portfolio`
 * returns the same answer at 200 KB because it counts on the server.
 *
 * What that gives up is evidence, deliberately. A reader checking one answer
 * wants every check behind it and should be looking at that building; a reader
 * looking at a portfolio wants none of them. So the per-building rule-by-rule
 * table that used to be at the bottom of this page is gone, and the buildings
 * page is where one building is opened.
 */

const VOCAB = PLACES.provider;

/** How many buildings to name before a count does the talking. */
const NAMED = 12;

export async function ProviderHome({ places, health }: { places: Place[]; health: Health }) {
  const [ruleStats, changeResults, portfolio] = await Promise.all([
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
    tryApi<Portfolio>("/api/accounts/me/portfolio"),
  ]);

  if (places.length === 0) {
    return (
      <div className="space-y-8">
        <Intro health={health} count={0} />
        <Notice title="No buildings on this account yet">
          {VOCAB.empty}{" "}
          <Link href="/places" className="underline" style={{ color: "var(--accent)" }}>
            Add a building
          </Link>
          .
        </Notice>
      </div>
    );
  }

  if (!ruleStats?.total) {
    return (
      <div className="space-y-8">
        <Intro health={health} count={places.length} />
        <NoRules />
      </div>
    );
  }

  if (!portfolio) {
    return (
      <div className="space-y-8">
        <Intro health={health} count={places.length} />
        <Notice title="The portfolio roll-up did not answer" tone="warning">
          Your buildings are listed on the{" "}
          <Link href="/places" className="underline">
            buildings page
          </Link>{" "}
          and each one still answers on its own. It is the figures across all of
          them that are missing.
        </Notice>
      </div>
    );
  }

  const byAddress = new Map(places.map((place) => [place.address_id, place]));
  const rolled = new Map(portfolio.buildings.map((row) => [row.address_id, row]));
  const named = (ids: string[]) =>
    ids.map((id) => byAddress.get(id)?.street_address ?? id).join(", ");

  const unverified = places.filter((place) => place.jurisdiction_status !== "resolved");
  const blocked = places
    .map((place) => ({ place, rolled: rolled.get(place.address_id) }))
    .filter((row): row is { place: Place; rolled: PortfolioBuilding } =>
      Boolean(row.rolled?.blocked_by.length),
    )
    .sort((a, b) => b.rolled.unknown - a.rolled.unknown);

  const moved = (changeResults ?? [])
    .filter((result) => !result.blocked_reason)
    .map((result) => ({
      result,
      hit: result.affected_address_ids.filter((id) => byAddress.has(id)),
    }))
    .filter((entry) => entry.hit.length > 0);
  // A case that could not be replayed has an empty affected set for want of a
  // computation, not because it misses this portfolio — so it is held apart and
  // counted rather than quietly joining the cases that found nothing.
  const unanswerable = (changeResults ?? []).filter((result) => result.blocked_reason);

  // Green for a building with a settled answer, amber for one waiting on a
  // fact. That is the provider's whole triage, and on a map it is the shape of
  // the portfolio rather than a column of numbers.
  const points = places
    .map((place) => {
      const row = rolled.get(place.address_id);
      return placePoint(place, row && row.blocked_by.length > 0 ? "blocked" : "applies", [
        `${row?.applies ?? 0} binding · ${row?.unknown ?? 0} unsettled`,
        row?.blocked_by.length
          ? `Waiting on ${row.blocked_by.map(fieldName).join(", ")}`
          : "Nothing blocked",
      ]);
    })
    .filter((point): point is NonNullable<typeof point> => Boolean(point));
  const missingPins = unplaced(places);
  const cities = [
    ...new Set(places.map((place) => place.legal_city).filter((c): c is string => Boolean(c))),
  ];

  return (
    <div className="space-y-10">
      <Intro health={health} count={places.length} />

      <StatStrip>
        <Stat
          label="Buildings"
          value={portfolio.totals.buildings}
          sub={`${portfolio.totals.evaluated} evaluated`}
        />
        <Stat
          label="Obligations in play"
          value={portfolio.categories.length}
          sub="distinct kinds, across the portfolio"
        />
        <Stat
          label="Fully answered"
          value={`${portfolio.totals.fully_answered} / ${portfolio.totals.evaluated}`}
          sub="no answer blocked by a missing fact"
          tone={
            portfolio.totals.fully_answered === portfolio.totals.evaluated ? "good" : undefined
          }
        />
        <Stat
          label="Tested, not binding"
          value={portfolio.totals.not_binding}
          sub="a local rule that misses on a building fact"
        />
      </StatStrip>

      {unverified.length > 0 && (
        <Notice
          title={`${unverified.length} building${unverified.length === 1 ? "" : "s"} has no verified legal city`}
          tone="warning"
        >
          City obligations cannot be settled for{" "}
          {named(unverified.slice(0, NAMED).map((p) => p.address_id))}
          {unverified.length > NAMED && ` and ${unverified.length - NAMED} more`} until
          the legal city is verified. The mailing city is never substituted for
          it — state law still answers normally.
        </Notice>
      )}

      {/* ----------------------------------------------------- the matrix */}
      <section>
        <MapPanel
          title="What binds each building"
          hint="One row per building, one column per kind of obligation. A cell says how many rules of that kind reach the building, and how many are still unsettled. The map is the same portfolio, coloured by whether anything is blocked."
          points={points}
          highlight={cities}
          shadeOnly
          legend={["applies", "blocked"]}
          labels={points.length <= 12}
          height={420}
          initialView={places.length > 1 ? "map" : "table"}
          footnote={
            missingPins > 0
              ? `${missingPins} of your ${places.length} could not be placed: the geocoder returned no coordinate, so there is no pin rather than an approximate one.`
              : undefined
          }
          table={
            <Table>
              <thead>
                <tr>
                  <Th>Building</Th>
                  <Th>Legal city</Th>
                  {portfolio.categories.map((category) => (
                    <Th key={category}>{categoryName(category)}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {places.map((place) => {
                  const row = rolled.get(place.address_id);
                  return (
                    <tr key={place.id}>
                      <Td>
                        <div className="font-medium">{place.label ?? place.street_address}</div>
                        {place.label && (
                          <div className="text-xs" style={{ color: "var(--faint)" }}>
                            {place.street_address}
                          </div>
                        )}
                        <div className="mt-1 text-xs">
                          <Facts place={place} />
                        </div>
                      </Td>
                      <Td className="whitespace-nowrap">
                        {place.jurisdiction_status === "resolved" ? (
                          place.legal_city
                        ) : (
                          <span style={{ color: "var(--warn)" }}>not verified</span>
                        )}
                      </Td>
                      {portfolio.categories.map((category) => {
                        const cell = row?.by_category[category];
                        return (
                          <Td key={category} className="tabular-nums whitespace-nowrap">
                            {!cell || (cell.binding === 0 && cell.unsettled === 0) ? (
                              <span style={{ color: "var(--faint)" }}>—</span>
                            ) : (
                              <>
                                {cell.binding > 0 && (
                                  <span style={{ color: "var(--good)", fontWeight: 600 }}>
                                    {cell.binding} binding
                                  </span>
                                )}
                                {cell.binding > 0 && cell.unsettled > 0 && <br />}
                                {cell.unsettled > 0 && (
                                  <span style={{ color: "var(--warn)" }}>
                                    {cell.unsettled} unsettled
                                  </span>
                                )}
                              </>
                            )}
                          </Td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          }
        />
      </section>

      {/* ---------------------------------------------- the missing facts */}
      <section>
        <SectionTitle
          title="What to go and find"
          hint="Ordered by how many answers supplying one fact would settle. This is the whole of the work queue — nothing else here is actionable by you."
        />
        {portfolio.blocking.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Nothing is blocked. Every rule that reaches your buildings has a
            settled answer on {health.default_as_of}.
          </p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Missing fact</Th>
                <Th>Answers it would settle</Th>
                <Th>Buildings</Th>
              </tr>
            </thead>
            <tbody>
              {portfolio.blocking.map((item) => (
                <tr key={item.field}>
                  <Td className="font-medium">{fieldName(item.field)}</Td>
                  <Td className="tabular-nums">{item.answers}</Td>
                  <Td>
                    <span className="tabular-nums">{item.buildings}</span>
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {named(item.address_ids)}
                      {item.buildings > item.address_ids.length &&
                        ` and ${item.buildings - item.address_ids.length} more`}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        <p className="mt-3 max-w-2xl text-sm" style={{ color: "var(--faint)" }}>
          These gaps are in the public records rather than in this app. Where
          coverage turns on a fact the data lacks, the answer stays{" "}
          <em>unknown</em> and names the field — it is never guessed in either
          direction.
        </p>
      </section>

      {/* --------------------------------------- what does not bind, and why */}
      <section className="space-y-4">
        <SectionTitle
          title="Tested and not binding"
          hint="A rule in force in a building's own jurisdiction that was checked against it and still does not reach it. Counted by rule rather than listed per building: “this rent cap was tested against 312 of yours and misses all of them on the unit count” is the compliance fact, and the same thing one building at a time is thousands of rows."
        />
        {portfolio.exemptions.length > 0 && (
          <ul className="space-y-3">
            {portfolio.exemptions.map((rule) => (
              <li
                key={`exempt-${rule.rule}`}
                className="rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-medium">
                    {rule.buildings} building{rule.buildings === 1 ? "" : "s"} exempt
                  </span>
                  <span className="text-sm" style={{ color: "var(--muted)" }}>
                    from {rule.title ?? rule.rule}
                  </span>
                  <span className="text-xs" style={{ color: "var(--accent)" }}>
                    exemption matched
                  </span>
                </div>
                {rule.why && (
                  <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
                    {rule.why}
                  </p>
                )}
                <div className="mt-2 text-xs" style={{ color: "var(--faint)" }}>
                  {named(rule.address_ids)}
                  {rule.buildings > rule.address_ids.length &&
                    ` and ${rule.buildings - rule.address_ids.length} more`}
                  {rule.citation ? ` · ${rule.citation}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}

        {portfolio.missed.length === 0 && portfolio.exemptions.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Every rule in force in your buildings&rsquo; own jurisdictions either
            binds them or is still unsettled. None was tested and definitively
            missed.
          </p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Rule that does not bind</Th>
                <Th>Buildings</Th>
                <Th>What it missed on</Th>
              </tr>
            </thead>
            <tbody>
              {portfolio.missed.map((rule) => (
                <tr key={`miss-${rule.rule}`}>
                  <Td>
                    <div className="font-medium">{rule.title ?? rule.rule}</div>
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {rule.jurisdiction} · {categoryName(rule.category)}
                    </div>
                  </Td>
                  <Td>
                    <span className="tabular-nums">{rule.buildings}</span>
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {named(rule.address_ids)}
                      {rule.buildings > rule.address_ids.length &&
                        ` and ${rule.buildings - rule.address_ids.length} more`}
                    </div>
                  </Td>
                  <Td>
                    <div style={{ maxWidth: "56ch" }}>{rule.why}</div>
                    {rule.citation && (
                      <div className="mt-1 text-xs" style={{ color: "var(--faint)" }}>
                        {rule.citation}
                      </div>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}

        {portfolio.exemptions.length === 0 && (
          <p className="max-w-2xl text-sm" style={{ color: "var(--faint)" }}>
            No exemption has let any of your buildings off a rule
            {portfolio.totals.untranslated_rules > 0 ? (
              <>
                {" "}
                — but {portfolio.totals.untranslated_rules} of the rules reaching
                them still carry text the adapter has not turned into a testable
                condition, and an exemption inside that text cannot be ruled in
                or out. Those rules answer <em>unsettled</em> rather than
                pretending the question was asked.
              </>
            ) : (
              ". Every exemption in a reaching rule was tested and none matched."
            )}
          </p>
        )}
      </section>

      {/* ------------------------------------------ the ones that need you */}
      <section className="space-y-4">
        <SectionTitle
          title="Buildings waiting on a fact"
          hint="The ones with an answer still blocked, worst first. A building with nothing blocked is not here — it needs nothing from you."
          right={<ManageLink label="Buildings and agreements" />}
        />
        {blocked.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Nothing is waiting. Every building has a settled answer for every
            rule that reaches it.
          </p>
        ) : (
          <>
            {blocked.slice(0, NAMED).map(({ place, rolled: row }) => (
              <Card key={place.id}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                  <div>
                    <div className="font-medium">{place.label ?? place.street_address}</div>
                    <div className="text-sm" style={{ color: "var(--muted)" }}>
                      <WhereItIs place={place} />
                    </div>
                    {place.note && (
                      <div className="mt-1 text-sm" style={{ color: "var(--faint)" }}>
                        {place.note}
                      </div>
                    )}
                  </div>
                  <div className="text-sm tabular-nums" style={{ color: "var(--muted)" }}>
                    {row.applies} binding · {row.unknown} unsettled
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      waiting on {row.blocked_by.map(fieldName).join(", ")}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
            {blocked.length > NAMED && (
              <p className="text-sm" style={{ color: "var(--faint)" }}>
                and {blocked.length - NAMED} more, in the matrix above and on the{" "}
                <Link href="/places" className="underline">
                  buildings page
                </Link>
                . Every rule considered for one building is on that building,
                where the checks behind each answer can be read with it.
              </p>
            )}
          </>
        )}
      </section>

      {/* ----------------------------------------------------- the changes */}
      <section className="space-y-4">
        <SectionTitle
          title="What the change cases do to you"
          hint="Each case is answered by re-running the same evaluator at the relevant dates, so an effect on your portfolio is computed rather than asserted."
          right={<ManageLink href="/changes" label="All change cases" />}
        />
        {moved.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            No change case that could be answered moves an answer at any of your
            buildings.
          </p>
        ) : (
          <ul className="space-y-3">
            {moved.map(({ result, hit }) => (
              <Card key={result.test_id}>
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="mono text-sm font-semibold">{result.test_id}</span>
                  <span className="font-medium">{result.title}</span>
                  <span className="text-xs" style={{ color: "var(--faint)" }}>
                    as of {result.as_of}
                  </span>
                </div>
                <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
                  {result.notes}
                </p>
                <p className="mt-2 text-sm">
                  <strong>Your buildings affected:</strong>{" "}
                  <span className="tabular-nums">{hit.length}</span>
                  {" — "}
                  {named(hit.slice(0, NAMED))}
                  {hit.length > NAMED && ` and ${hit.length - NAMED} more`}
                </p>
              </Card>
            ))}
          </ul>
        )}
        {unanswerable.length > 0 && (
          <p className="text-sm" style={{ color: "var(--warn)" }}>
            {unanswerable.length} of the cases cannot be answered from the
            records we hold yet, so they are not counted above.{" "}
            <Link href="/changes" className="underline">
              The change cases page
            </Link>{" "}
            says which rule each one is waiting on.
          </p>
        )}
      </section>
    </div>
  );
}

function Intro({ health, count }: { health: Health; count: number }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">
        {count === 0
          ? "Your portfolio"
          : `Your portfolio — ${count} building${count === 1 ? "" : "s"}`}
      </h1>
      <p className="mt-2 max-w-2xl leading-relaxed" style={{ color: "var(--muted)" }}>
        {ROLES.provider.cardinalityNote} Everything below is answered as of{" "}
        <strong className="mono">{health.default_as_of}</strong>.
      </p>
    </section>
  );
}
