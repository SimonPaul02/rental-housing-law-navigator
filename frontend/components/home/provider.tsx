import Link from "next/link";
import {
  Card,
  Expand,
  Notice,
  ResultBadge,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { MapPanel } from "@/components/map/map-panel";
import { tryApi } from "@/lib/api";
import { answered, blocked } from "@/lib/changes";
import { placePoint, unplaced } from "@/lib/points";
import {
  blockingFacts,
  categoriesOf,
  categoryName,
  claimsExemption,
  evaluate,
  exemptionCheck,
  fieldName,
  split,
} from "@/lib/lookups";
import { PLACES, ROLES } from "@/lib/roles";
import type {
  ChangeTestResult,
  Health,
  LookupResponse,
  Place,
  RuleOutcome,
  RuleStats,
} from "@/lib/types";
import { Facts, ManageLink, NoRules, WhereItIs } from "./shared";

/** The provider's app: a portfolio, answered together.
 *
 * A housing provider has many buildings and is answerable for all of them at
 * once, so the page is the roll-up and a single building is the drill-down —
 * the exact inverse of the renter's. Three things follow from that, and they
 * are the three sections nobody else gets:
 *
 * 1. A matrix, because "which of my buildings is subject to a rent cap" is a
 *    question about a column, not about any one building.
 * 2. A missing-fact queue ordered by how many answers each field would
 *    unblock, because that is the only ordering that says what to go and find
 *    first.
 * 3. The exemptions each building claims — which needs the rules that do *not*
 *    bind it, the one caller for the API's `include_not_applicable`.
 */

const VOCAB = PLACES.provider;

export async function ProviderHome({ places, health }: { places: Place[]; health: Health }) {
  const [ruleStats, changeResults] = await Promise.all([
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
  ]);

  if (places.length === 0) {
    return (
      <div className="space-y-8">
        <Intro health={health} count={0} />
        <Notice title="No buildings added yet">
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

  // The whole portfolio in one request, with the rules that do not bind each
  // building included so the exemption column can be filled in.
  const lookups = await evaluate(places, { includeNotApplicable: true });
  const rows = places.map((place) => ({
    place,
    lookup: lookups.get(place.address_id),
    ...split(lookups.get(place.address_id)),
  }));

  const evaluated = rows.filter((row) => row.lookup);
  const categories = categoriesOf(
    evaluated.flatMap((row) => [...row.applies, ...row.unknown]),
  );
  const queue = blockingFacts(
    evaluated.map((row) => row.lookup).filter((l): l is LookupResponse => Boolean(l)),
  );
  const byAddress = new Map(rows.map((row) => [row.place.address_id, row]));
  const unverified = rows.filter((row) => row.place.jurisdiction_status !== "resolved");
  const clear = evaluated.filter((row) => row.unknown.length === 0).length;
  // Rules that reach this jurisdiction and were tested against the building,
  // and still do not bind it. The out-of-jurisdiction ones are dropped: "a
  // Berkeley ordinance does not cover your Boston building" is not compliance
  // information, it is a map, and there are eighty of them per address.
  const notBinding = evaluated.flatMap((row) =>
    row.notApplicable
      .filter((outcome) => outcome.in_jurisdiction)
      .map((outcome) => ({ place: row.place, outcome })),
  );
  const exemptions = notBinding.filter(({ outcome }) => claimsExemption(outcome));
  const onFacts = notBinding.filter(({ outcome }) => !claimsExemption(outcome));

  // How many of the rules reaching these buildings still carry prose the
  // adapter could not turn into a condition. Read off the traces rather than
  // fetched, and worth stating plainly: a rule in this state cannot be tested,
  // so neither its coverage nor its exemptions can be ruled out here.
  const untranslated = new Set(
    evaluated.flatMap((row) =>
      [...row.applies, ...row.unknown, ...row.notApplicable]
        .filter(
          (outcome) =>
            outcome.in_jurisdiction &&
            outcome.checks.some((check) => check.reason === "rule_clause_unmapped"),
        )
        .map((outcome) => outcome.team_rule_id),
    ),
  ).size;

  // A blocked case's empty set is not "it moves none of yours"; it is no answer.
  const notComputed = blocked(changeResults);
  const moved = answered(changeResults)
    .map((result) => ({
      result,
      hit: result.affected_address_ids.filter((id) => byAddress.has(id)),
    }))
    .filter((entry) => entry.hit.length > 0);

  // Green for a building with a settled answer, amber for one waiting on a
  // fact. That is the provider's whole triage, and on a map it is the shape of
  // the portfolio rather than a column of numbers.
  const points = rows
    .map((row) =>
      placePoint(
        row.place,
        row.unknown.length > 0 ? "blocked" : "applies",
        [
          `${row.applies.length} binding · ${row.unknown.length} unsettled`,
          row.unknown.length > 0
            ? `Waiting on ${[
                ...new Set(row.unknown.flatMap((o) => o.unresolved_fields)),
              ]
                .map(fieldName)
                .join(", ") || "a fact the record does not carry"}`
            : "Nothing blocked",
          `${row.notApplicable.filter((o) => o.in_jurisdiction).length} local rules tested and missed`,
        ],
      ),
    )
    .filter((point): point is NonNullable<typeof point> => Boolean(point));
  const missingPins = unplaced(places);
  const cities = [
    ...new Set(places.map((place) => place.legal_city).filter((c): c is string => Boolean(c))),
  ];

  return (
    <div className="space-y-10">
      <Intro health={health} count={places.length} />

      <StatStrip>
        <Stat label="Buildings" value={places.length} sub={`${evaluated.length} evaluated`} />
        <Stat
          label="Obligations in play"
          value={categories.length}
          sub="distinct kinds, across the portfolio"
        />
        <Stat
          label="Fully answered"
          value={`${clear} / ${evaluated.length}`}
          sub="no answer blocked by a missing fact"
          tone={clear === evaluated.length ? "good" : undefined}
        />
        <Stat
          label="Tested, not binding"
          value={notBinding.length}
          sub="a local rule that misses on a building fact"
        />
      </StatStrip>

      {unverified.length > 0 && (
        <Notice title={`${unverified.length} building${unverified.length === 1 ? "" : "s"} has no verified legal city`} tone="warning">
          City obligations cannot be settled for{" "}
          {unverified.map((row) => row.place.street_address).join(", ")} until the
          legal city is verified. The mailing city is never substituted for it —
          state law still answers normally.
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
          table={<Table>
          <thead>
            <tr>
              <Th>Building</Th>
              <Th>Legal city</Th>
              {categories.map((category) => (
                <Th key={category}>{categoryName(category)}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.place.id}>
                <Td>
                  <div className="font-medium">{row.place.label ?? row.place.street_address}</div>
                  {row.place.label && (
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {row.place.street_address}
                    </div>
                  )}
                  <div className="mt-1 text-xs">
                    <Facts place={row.place} />
                  </div>
                </Td>
                <Td className="whitespace-nowrap">
                  {row.place.jurisdiction_status === "resolved" ? (
                    row.place.legal_city
                  ) : (
                    <span style={{ color: "var(--warn)" }}>not verified</span>
                  )}
                </Td>
                {categories.map((category) => {
                  const binding = row.applies.filter(
                    (o) => (o.category ?? "uncategorised") === category,
                  ).length;
                  const open = row.unknown.filter(
                    (o) => (o.category ?? "uncategorised") === category,
                  ).length;
                  return (
                    <Td key={category} className="tabular-nums whitespace-nowrap">
                      {binding === 0 && open === 0 ? (
                        <span style={{ color: "var(--faint)" }}>—</span>
                      ) : (
                        <>
                          {binding > 0 && (
                            <span style={{ color: "var(--good)", fontWeight: 600 }}>
                              {binding} binding
                            </span>
                          )}
                          {binding > 0 && open > 0 && <br />}
                          {open > 0 && (
                            <span style={{ color: "var(--warn)" }}>{open} unsettled</span>
                          )}
                        </>
                      )}
                    </Td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </Table>}
        />
      </section>

      {/* ---------------------------------------------- the missing facts */}
      <section>
        <SectionTitle
          title="What to go and find"
          hint="Ordered by how many answers supplying one fact would settle. This is the whole of the work queue — nothing else here is actionable by you."
        />
        {queue.length === 0 ? (
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
              {queue.map((item) => (
                <tr key={item.field}>
                  <Td className="font-medium">{fieldName(item.field)}</Td>
                  <Td className="tabular-nums">{item.blocked}</Td>
                  <Td>
                    {item.addresses
                      .map((id) => byAddress.get(id)?.place.street_address ?? id)
                      .join(", ")}
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
          hint="A rule in force in this building's own jurisdiction that was checked against it and still does not reach it. Compliance needs the negatives as much as the positives: “no rent cap applies here” is only worth anything if you can see which rent cap was tested and what it missed on."
        />
        {exemptions.length > 0 && (
          <ul className="space-y-3">
            {exemptions.map(({ place, outcome }) => (
              <li
                key={`exempt-${place.id}-${outcome.team_rule_id}`}
                className="rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-medium">{place.label ?? place.street_address}</span>
                  <span className="text-sm" style={{ color: "var(--muted)" }}>
                    is exempt from {outcome.title ?? outcome.team_rule_id}
                  </span>
                  <span className="text-xs" style={{ color: "var(--accent)" }}>
                    exemption matched
                  </span>
                </div>
                <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
                  {exemptionCheck(outcome)?.detail ?? outcome.explanation}
                </p>
                {outcome.citation && (
                  <div className="mt-2 text-xs" style={{ color: "var(--faint)" }}>
                    {outcome.citation}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {onFacts.length === 0 && exemptions.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Every rule in force in your buildings&rsquo; own jurisdictions either
            binds them or is still unsettled. None was tested and definitively
            missed.
          </p>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Building</Th>
                <Th>Rule that does not bind it</Th>
                <Th>What it missed on</Th>
              </tr>
            </thead>
            <tbody>
              {onFacts.map(({ place, outcome }) => (
                <tr key={`miss-${place.id}-${outcome.team_rule_id}`}>
                  <Td>{place.label ?? place.street_address}</Td>
                  <Td>
                    <div className="font-medium">{outcome.title ?? outcome.team_rule_id}</div>
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {outcome.jurisdiction} · {categoryName(outcome.category)}
                    </div>
                  </Td>
                  <Td>
                    <div style={{ maxWidth: "56ch" }}>{outcome.explanation}</div>
                    {outcome.citation && (
                      <div className="mt-1 text-xs" style={{ color: "var(--faint)" }}>
                        {outcome.citation}
                      </div>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}

        {exemptions.length === 0 && (
          <p className="max-w-2xl text-sm" style={{ color: "var(--faint)" }}>
            No exemption has let any of your buildings off a rule
            {untranslated > 0 ? (
              <>
                {" "}— but {untranslated} of the rules reaching them still carry
                text the adapter has not turned into a testable condition, and
                an exemption inside that text cannot be ruled in or out. Those
                rules answer <em>unsettled</em> rather than pretending the
                question was asked.
              </>
            ) : (
              ". Every exemption in a reaching rule was tested and none matched."
            )}
          </p>
        )}
      </section>

      {/* ------------------------------------------ building by building */}
      <section className="space-y-4">
        <SectionTitle
          title="Building by building"
          hint="The same answers, one building at a time, for when the question is about one of them. Agreements are filed per unit on the buildings page."
          right={<ManageLink label={`Buildings and agreements`} />}
        />
        {rows.map((row) => (
          <Card key={row.place.id}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
              <div>
                <div className="font-medium">
                  {row.place.label ?? row.place.street_address}
                </div>
                <div className="text-sm" style={{ color: "var(--muted)" }}>
                  <WhereItIs place={row.place} />
                </div>
                {row.place.note && (
                  <div className="mt-1 text-sm" style={{ color: "var(--faint)" }}>
                    {row.place.note}
                  </div>
                )}
              </div>
              <div className="text-sm tabular-nums" style={{ color: "var(--muted)" }}>
                {row.applies.length} binding · {row.unknown.length} unsettled ·{" "}
                {row.notApplicable.filter((o) => o.in_jurisdiction).length} tested and missed
                <div className="text-xs" style={{ color: "var(--faint)" }}>
                  {row.place.contract_count
                    ? `${row.place.contract_count} tenancy ${
                        row.place.contract_count === 1 ? "agreement" : "agreements"
                      } on file`
                    : "No tenancy agreement on file"}
                </div>
              </div>
            </div>
            <div className="mt-3">
              <Expand label="Every rule considered">
                <Considered
                  outcomes={[...row.applies, ...row.unknown, ...row.coming, ...row.superseded]}
                />
              </Expand>
            </div>
          </Card>
        ))}
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
            None of the change cases
            {notComputed.length > 0 ? " that could be computed" : ""} moves an answer
            at any of your buildings.
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
                  {hit.map((id) => byAddress.get(id)?.place.street_address ?? id).join(", ")}
                </p>
              </Card>
            ))}
          </ul>
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

/** Every rule that was considered for one building, with its verdict.
 *
 * Compliance work needs the negatives as much as the positives: "no rent cap
 * reaches this building" is only trustworthy if you can see that the rent-cap
 * rules were tested and why each one missed.
 */
function Considered({ outcomes }: { outcomes: RuleOutcome[] }) {
  if (outcomes.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--faint)" }}>
        No rule in the corpus reaches this building on this date.
      </p>
    );
  }
  return (
    <Table>
      <thead>
        <tr>
          <Th>Rule</Th>
          <Th>Verdict</Th>
          <Th>Why</Th>
        </tr>
      </thead>
      <tbody>
        {outcomes.map((outcome) => (
          <tr key={outcome.team_rule_id}>
            <Td>
              <div className="font-medium">{outcome.title ?? outcome.team_rule_id}</div>
              <div className="text-xs" style={{ color: "var(--faint)" }}>
                {outcome.jurisdiction} · {categoryName(outcome.category)}
                {outcome.key_value ? ` · ${outcome.key_value}` : ""}
              </div>
            </Td>
            <Td>
              <ResultBadge result={outcome.result} />
            </Td>
            <Td>
              <div style={{ maxWidth: "52ch" }}>{outcome.explanation}</div>
              {outcome.citation && (
                <div className="mt-1 text-xs" style={{ color: "var(--faint)" }}>
                  {outcome.citation}
                </div>
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
