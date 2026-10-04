"use client";

import { useMemo, useState } from "react";
import { LazyAddressMap as AddressMap } from "@/components/map/lazy-map";
import { ViewToggle, type View } from "@/components/view-toggle";
import {
  Card,
  JurisdictionBadge,
  Notice,
  ResultBadge,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { MapPoint } from "@/components/map/model";
import { statusOf } from "@/lib/changes";
import { placeable } from "./filters";
import type {
  AddressRecord,
  ChangeDetail,
  ChangeTest,
  ChangeTestResult,
  LookupResult,
} from "@/lib/types";

/** The five change cases, each answering itself.
 *
 * The question behind this page is not "how many addresses does T3 touch". It
 * is *what changed, for whom, and from what to what* — so a case has to say
 * which law moved, between which two dates, and what the answer was on either
 * side of them. A count alone cannot: "184 affected" reads the same whether a
 * law came into force, a bill might pass next year, or a ballot question was
 * voted down, and those are three different facts about a building.
 *
 * Hence three things this view does that a count beside a chip did not:
 *
 * **Every case states its answer up front.** All five headlines are readable
 * without clicking, because they are five findings to compare, not five
 * filters to choose between. Opening one adds the working, not the answer.
 *
 * **"Affected" is never printed bare.** Each kind of case means something
 * different by it — an answer that changed, a rule that reaches, a forecast if
 * a bill passes, or a guard that should match nobody — so each kind labels its
 * own number and says in a sentence how to read it.
 *
 * **A case that cannot run says why, where it is.** One unextracted ordinance
 * used to blank the whole page; now it blanks its own case and names the id to
 * go and fix.
 *
 * The map still earns its place: that T3's buildings are all in two cities is
 * the thing a reader wants to know about a state law, and the thing a count
 * cannot tell them. A case whose buildings have no coordinates still reports
 * its number and says how many it could not draw.
 */

/** How to read each kind of case, in the page's own words.
 *
 * The backend's `type` is a four-value vocabulary (`as_of`, `boundary`,
 * `pending`, `negative`) that means nothing to anyone who has not read
 * `service.py`. Every string a reader sees comes from here instead.
 */
const KINDS: Record<
  string,
  {
    /** The badge on the case, in plain words. */
    badge: string;
    /** What this kind of case does, and how to read its number. */
    blurb: string;
    /** What the headline number counts. */
    counts: string;
  }
> = {
  as_of: {
    badge: "a law taking effect",
    blurb:
      "This law is already on the books with a date it starts to bite. The case asks the same question twice — once before that date, once after — and reports the buildings whose answer is different the second time.",
    counts: "Answers that change",
  },
  boundary: {
    badge: "a city limit",
    blurb:
      "Two city ordinances, each binding only inside its own city. The case asks one date and reports which buildings each rule reaches, so an ordinance leaking across the line would show up here as a building claimed by both.",
    counts: "Buildings reached",
  },
  pending: {
    badge: "a bill, not yet law",
    blurb:
      "A bill that has not been enacted, so it binds nobody today. The case reports who would fall in scope if it passed — a forecast, kept deliberately apart from what applies now.",
    counts: "In scope if it passed",
  },
  negative: {
    badge: "a measure that failed",
    blurb:
      "This measure was voted down, so the correct answer is that it applies to nobody. The case is a guard rather than a finding: it passes by matching nothing, and the replay refuses to report at all if it matches anything.",
    counts: "Buildings affected",
  },
};

function kindOf(type: string) {
  return (
    KINDS[type] ?? {
      badge: type,
      blurb: "This kind of case has no explanation written for it yet.",
      counts: "Buildings affected",
    }
  );
}

const REPORTABLE: LookupResult[] = [
  "applies",
  "unknown",
  "superseded",
  "not_yet_effective",
  "pending",
  "does_not_apply",
];

/** The evaluator's answers are a closed set, but they arrive as loose strings.
 *  Anything outside it is printed as itself rather than silently badged wrong. */
function asResult(value: string): LookupResult | null {
  return (REPORTABLE as string[]).includes(value) ? (value as LookupResult) : null;
}

function Answer({ value }: { value: string }) {
  const known = asResult(value);
  return known ? <ResultBadge result={known} /> : <span className="mono">{value}</span>;
}

/** The case's answer as one sentence, before anything is opened.
 *
 *  Written out per kind rather than assembled from a template. "none of 250"
 *  and "none, and that is the expected answer" are the same arithmetic and
 *  opposite news, and no amount of shared phrasing would let one sentence say
 *  both — so each kind gets its own sentence, and both of its endings.
 */
function headline(test: ChangeTest, result: ChangeTestResult | undefined): string {
  if (!result) return "Not replayed yet.";
  if (result.blocked_reason) return "Cannot be answered from the current records.";

  const detail: ChangeDetail = result.detail ?? {};
  const n = result.affected_address_ids.length;
  const scanned = detail.addresses_examined ?? 0;
  const where = test.states?.length ? `${test.states.join(" and ")} ` : "";
  const population = scanned
    ? `the ${scanned} ${where}addresses scanned`
    : `the ${where}addresses scanned`;
  // "250 of the 250" is arithmetic where "All 250" is the finding.
  const subject =
    scanned > 0 && n === scanned
      ? `All ${scanned} ${where}addresses scanned`
      : `${n} of ${population}`;

  // The dates come from the case definition, so they are on every as_of result
  // the backend builds — but a sentence reading "between undefined and
  // undefined" is a worse way to discover that than a vaguer phrase.
  const before = detail.as_of_before ?? test.as_of_before ?? "the earlier date";
  const after = detail.as_of_after ?? test.as_of_after ?? result.as_of;

  switch (test.type) {
    case "as_of":
      return n === 0
        ? `None of ${population} answers differently on ${after} than on ${before}.`
        : `${subject} answer differently on ${after} than on ${before}.`;
    case "boundary":
      return n === 0
        ? `No address among ${population} is reached by either ordinance on ${result.as_of}.`
        : `${subject} are reached by one of these ordinances on ${result.as_of}.`;
    case "pending":
      return n === 0
        ? `Not law on ${result.as_of}, and none of ${population} would fall in scope if it passed.`
        : `Not law on ${result.as_of}. ${subject} would fall in scope if it passed.`;
    case "negative":
      return n === 0
        ? `Nothing applies to any of ${population}, which is the expected answer for a measure that failed.`
        : `${n} addresses still report this failed measure — a defect, not a finding.`;
    default:
      return result.notes;
  }
}

/** Rule statuses as a sentence, for explaining a case that moved nothing.
 *
 *  `rule_status` carries an object for an as_of case and a bare status string
 *  for the others, so both shapes are read here rather than at each call site.
 */
function statusSentence(detail: ChangeDetail): string {
  const entries = Object.entries(detail.rule_status ?? {});
  if (entries.length === 0) return "No rule status was recorded.";
  const parts = entries
    .map(([ruleId, value]) => {
      if (typeof value === "string") return `${ruleId} is ${value.replace(/_/g, " ")}`;
      const date = value.effective_date;
      return `${ruleId} is ${value.status.replace(/_/g, " ")} ${
        date ? `and takes effect ${date}` : "with no effective date recorded"
      }`;
    })
    .join("; ");
  return `${parts}.`;
}

export function ChangesExplorer({
  tests,
  results,
  addresses,
  initialAddressId = "",
}: {
  tests: ChangeTest[];
  results: ChangeTestResult[];
  /** Only the addresses some case touches — the whole sample is not needed. */
  addresses: AddressRecord[];
  /** A building from `?address_id=`, pre-selected so the link lands on it. */
  initialAddressId?: string;
}) {
  const resultsById = useMemo(
    () => new Map(results.map((result) => [result.test_id, result])),
    [results],
  );

  // Open on a case that actually has an answer. A blocked case and an empty
  // one are both legitimate things to land on, but neither shows a reader what
  // this page is for — and locally, where only part of the corpus is
  // extracted, the first case is often one of them.
  const opening = useMemo(() => {
    const answered = (test: ChangeTest) => {
      const result = resultsById.get(test.test_id);
      return result && !result.blocked_reason;
    };
    const touchesLink = (test: ChangeTest) =>
      Boolean(initialAddressId) &&
      resultsById.get(test.test_id)?.affected_address_ids.includes(initialAddressId);

    const moved = (test: ChangeTest) => {
      const result = resultsById.get(test.test_id);
      return Boolean(result) && !result!.blocked_reason && result!.affected_address_ids.length > 0;
    };

    return tests.find(touchesLink) ?? tests.find(moved) ?? tests.find(answered) ?? tests[0];
  }, [tests, resultsById, initialAddressId]);

  const [open, setOpen] = useState<string>(opening?.test_id ?? "");
  const [view, setView] = useState<View>("map");
  const [pin, setPin] = useState<string | null>(initialAddressId || null);

  const byId = useMemo(
    () => new Map(addresses.map((address) => [address.address_id, address])),
    [addresses],
  );

  return (
    <section className="space-y-5">
      <SectionTitle
        title="The five change cases"
        hint="All five answers, readable at once. Open one for its dates, the buildings it moves, what the brief asked of it and the law it read."
      />

      <ol className="case-list">
        {tests.map((test) => {
          const result = resultsById.get(test.test_id);
          const isOpen = open === test.test_id;
          const kind = kindOf(test.type);
          const blocked = Boolean(result?.blocked_reason);
          // A blocked case computed no conflicts either, so its zero is not a
          // finding worth a line in the headline.
          const flagged = blocked ? 0 : (result?.conflict_flag_address_ids.length ?? 0);
          // An answer that stands but is missing a part the case asks for -
          // T3 without its conflict check, T5 without its failed-measure
          // record - says so before it is opened, not only inside.
          const partial = result ? statusOf(result) === "partial" : false;
          return (
            <li key={test.test_id} className={`case${isOpen ? " open" : ""}`}>
              <h3>
                <button
                  type="button"
                  className="case-head"
                  aria-expanded={isOpen}
                  onClick={() => {
                    setOpen(isOpen ? "" : test.test_id);
                    setPin(null);
                  }}
                >
                  <span className="case-top">
                    <span className="mono case-id">{test.test_id}</span>
                    <span className="case-title">{test.title}</span>
                    <span className="case-kind">{kind.badge}</span>
                  </span>
                  <span className={`case-answer${blocked ? " blocked" : ""}`}>
                    {headline(test, result)}
                    {flagged > 0 && (
                      <span className="case-flagged">
                        {flagged} {flagged === 1 ? "needs" : "need"} a person to
                        settle which law wins
                      </span>
                    )}
                    {partial && (
                      <span className="case-flagged">
                        Partly answered — open it for what is still missing
                      </span>
                    )}
                  </span>
                </button>
              </h3>

              {isOpen && (
                <div className="case-body">
                  <CaseBody
                    test={test}
                    result={result}
                    byId={byId}
                    view={view}
                    onView={setView}
                    pin={pin}
                    onPin={setPin}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** One opened case: how to read it, what the replay found, and its working. */
function CaseBody({
  test,
  result,
  byId,
  view,
  onView,
  pin,
  onPin,
}: {
  test: ChangeTest;
  result: ChangeTestResult | undefined;
  byId: Map<string, AddressRecord>;
  view: View;
  onView: (next: View) => void;
  pin: string | null;
  onPin: (next: string | null) => void;
}) {
  const kind = kindOf(test.type);

  const affected = useMemo(() => {
    if (!result) return [];
    return result.affected_address_ids
      .map((id) => byId.get(id))
      .filter((address): address is AddressRecord => Boolean(address));
  }, [result, byId]);

  const conflicted = useMemo(
    () => new Set(result?.conflict_flag_address_ids ?? []),
    [result],
  );

  const points = useMemo<MapPoint[]>(
    () =>
      affected.filter(placeable).map((address) => ({
        id: address.address_id,
        lon: address.longitude as number,
        lat: address.latitude as number,
        title: address.street_address,
        subtitle:
          address.jurisdiction_status === "resolved"
            ? `${address.legal_city}, ${address.legal_state ?? address.state}`
            : `${address.postal_city}, ${address.state} — mailing city`,
        jurisdiction: address.legal_city,
        state: address.state,
        tone: conflicted.has(address.address_id) ? "conflict" : "affected",
        detail: [
          address.year_built ? `Built ${address.year_built}` : "Year built not in the record",
          address.units ? `${address.units} units` : "Unit count not in the record",
          conflicted.has(address.address_id)
            ? "Which law wins here is for a person to settle"
            : "The answer moves cleanly",
        ],
      })),
    [affected, conflicted],
  );

  /** Which cities the case lands in, so the map can shade their extent. */
  const cities = useMemo(
    () => [...new Set(affected.map((a) => a.legal_city).filter((c): c is string => Boolean(c)))],
    [affected],
  );

  if (!result) {
    return (
      <p className="case-note">
        This case has not been replayed. <code className="mono">POST
        /api/change-tracking/tests/{test.test_id}/run</code> runs it.
      </p>
    );
  }

  if (statusOf(result) === "blocked") {
    // The reason is not always a missing rule: a date case whose rule has no
    // usable effective date, or a failed guardrail, blocks a case too, and the
    // reason names which. Only the missing-rule kind is fixed by extraction.
    const reason = (result.blocked_reason ?? "").replace(/\.?$/, ".");
    return (
      <>
        <p className="case-blurb">{kind.blurb}</p>
        <Notice title="This case cannot be answered from the records we hold" tone="warning">
          {reason}{" "}
          {result.rules_resolved
            ? "Until that is fixed there is nothing for the case to replay"
            : "Until those rules are extracted and their quoted spans verified against their sources, there is nothing for the case to replay"}{" "}
          — said here rather than reported as an affected set of nobody, because
          those are different answers.
        </Notice>
      </>
    );
  }

  const detail: ChangeDetail = result.detail ?? {};
  const scanned = detail.addresses_examined ?? 0;
  const affectedCount = result.affected_address_ids.length;
  const undrawable = affected.length - points.length;
  const missingRows = affectedCount - affected.length;
  const perRule = Object.entries(detail.per_rule ?? {});
  const unplaced = (detail.legal_city_unresolved ?? []).length;

  return (
    <>
      <p className="case-blurb">{kind.blurb}</p>

      {/* The dates, which are what the case is actually about. */}
      {test.type === "as_of" && (detail.as_of_before ?? test.as_of_before) ? (
        <div className="case-dates">
          <div>
            <span>asked first on</span>
            <strong className="mono">{detail.as_of_before ?? test.as_of_before}</strong>
            <em>before the law bites</em>
          </div>
          <span className="case-arrow" aria-hidden>
            →
          </span>
          <div>
            <span>then again on</span>
            <strong className="mono">{detail.as_of_after ?? test.as_of_after}</strong>
            <em>and the two answers compared</em>
          </div>
        </div>
      ) : (
        <div className="case-dates one">
          <div>
            <span>asked as of</span>
            <strong className="mono">{result.as_of}</strong>
          </div>
        </div>
      )}

      <StatStrip>
        <Stat
          label={kind.counts}
          value={affectedCount}
          sub={scanned ? `of ${scanned} scanned` : undefined}
          tone={test.type === "negative" && affectedCount > 0 ? "critical" : undefined}
        />
        {test.type === "as_of" && (
          <>
            <Stat
              label="Covered outright after"
              value={detail.covered_after ?? 0}
              sub="the rule reaches them"
            />
            <Stat
              label="Coverage unresolved"
              value={detail.coverage_unresolved_after ?? 0}
              sub="in force, but a missing fact decides it"
              tone={(detail.coverage_unresolved_after ?? 0) > 0 ? "warning" : undefined}
            />
          </>
        )}
        {test.type === "pending" && (
          <Stat label="In force today" value="none" sub="it is not law yet" />
        )}
        {test.type === "boundary" && (
          <Stat
            label="Claimed by both"
            value={(detail.overlap ?? []).length}
            sub="should be none across a city line"
            tone={(detail.overlap ?? []).length > 0 ? "critical" : undefined}
          />
        )}
        {test.type === "boundary" && detail.legal_city_unresolved && (
          <Stat
            label="No verified city"
            value={unplaced}
            sub="left out, never placed by the mailing city"
            tone={unplaced > 0 ? "warning" : undefined}
          />
        )}
      </StatStrip>

      {/* What the brief asked for, beside what the replay found. Kept as two
          statements rather than a verdict: the expectation is prose written for
          a human, so claiming a pass or fail against it would be the page
          asserting something it cannot check. */}
      <div className="case-check">
        <div>
          <span>What the brief asks for</span>
          <p>{test.expected_behavior}</p>
        </div>
        <div>
          <span>What the replay found</span>
          <p>{result.notes}</p>
        </div>
      </div>

      {statusOf(result) === "partial" && (
        <Notice title="Partly answered" tone="warning">
          The answer above stands, but part of what this case asks for could not
          be produced: {(result.warnings ?? []).join(" ")}
        </Notice>
      )}

      {perRule.length > 0 && (
        <ul className="case-rules">
          {perRule.map(([canonicalId, ids]) => {
            const where = [
              ...new Set(
                ids
                  .map((id) => byId.get(id)?.legal_city)
                  .filter((city): city is string => Boolean(city)),
              ),
            ];
            return (
              <li key={canonicalId}>
                <span className="mono">{canonicalId}</span>
                <span>
                  {test.type === "pending" ? "would reach" : "reaches"}{" "}
                  <strong className="tabular-nums">{ids.length}</strong>{" "}
                  {ids.length === 1 ? "building" : "buildings"}
                  {where.length > 0 &&
                    (where.length > 1
                      ? `, across ${where.join(", ")}`
                      : ids.length === 1
                        ? `, in ${where[0]}`
                        : `, all in ${where[0]}`)}
                  {detail.per_rule_split?.[canonicalId] &&
                    ` — ${detail.per_rule_split[canonicalId].applies.length} covered outright, ${detail.per_rule_split[canonicalId].coverage_unresolved.length} waiting on a building fact`}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {/* A date case that moved nothing is usually a record problem, not a
          finding, and the status line says which. */}
      {test.type === "as_of" && affectedCount === 0 && scanned > 0 && (
        <Notice title="Nothing moved between the two dates" tone="warning">
          A case like this can only show a transition if the rule record carries
          the date it comes into force. {statusSentence(detail)} Check the
          extracted effective date against the source before reading this as the
          law&apos;s own answer.
        </Notice>
      )}

      {result.conflict_flag_address_ids.length > 0 && (
        <Notice
          title={
            result.conflict_flag_address_ids.length === 1
              ? "One building where two laws overlap"
              : `${result.conflict_flag_address_ids.length} buildings where two laws overlap`
          }
          tone="warning"
        >
          A local ban is already in force {result.conflict_flag_address_ids.length === 1
            ? "there"
            : "at these addresses"}{" "}
          and the arriving state law may or may not override it. The flag
          changes nobody&apos;s answer — it marks the question for a person,
          because guessing at preemption is not something this system should do.
        </Notice>
      )}

      {test.type === "negative" && affectedCount === 0 && (
        <p className="case-note">
          The replay also checked that no Massachusetts rent cap is live at any
          sampled address. Had either check matched, this case would have
          refused to report rather than returning an empty set — an empty
          answer and an unasked question look identical from the outside.
        </p>
      )}

      {affectedCount > 0 && (
        <>
          <div className="case-mapbar">
            <p>
              {test.type === "pending" ? "Who would fall in scope" : "Which buildings"}
              {cities.length > 0 && (
                <>
                  {" — "}
                  {cities.length === 1 ? "all in " : `across ${cities.length} cities: `}
                  {cities.slice(0, 4).join(", ")}
                  {cities.length > 4 ? ` and ${cities.length - 4} more` : ""}
                </>
              )}
            </p>
            <ViewToggle view={view} onChange={onView} label="Affected addresses view" />
          </div>

          {(undrawable > 0 || missingRows > 0) && (
            <p className="case-note warn">
              {undrawable > 0 && `${undrawable} have no coordinate and cannot be drawn. `}
              {missingRows > 0 &&
                `${missingRows} are counted above but are not in the loaded sample.`}
            </p>
          )}

          {view === "map" ? (
            <AddressMap
              points={points}
              highlight={cities.slice(0, 6)}
              shadeOnly
              selectedId={pin}
              onSelect={onPin}
              legend={["affected", "conflict"]}
              height={440}
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>ID</Th>
                  <Th>Address</Th>
                  <Th>Legal city</Th>
                  <Th>Jurisdiction</Th>
                  <Th>Built</Th>
                  <Th>Units</Th>
                  <Th>On this case</Th>
                </tr>
              </thead>
              <tbody>
                {affected.map((address) => (
                  <tr
                    key={address.address_id}
                    className={`clickable${pin === address.address_id ? " picked-row" : ""}`}
                    onClick={() => onPin(pin === address.address_id ? null : address.address_id)}
                  >
                    <Td className="mono">{address.address_id}</Td>
                    <Td>{address.street_address}</Td>
                    <Td>{address.legal_city ?? address.postal_city}</Td>
                    <Td>
                      <JurisdictionBadge status={address.jurisdiction_status} />
                    </Td>
                    <Td className="tabular-nums">{address.year_built ?? "—"}</Td>
                    <Td className="tabular-nums">{address.units ?? "—"}</Td>
                    <Td className="text-xs">
                      {conflicted.has(address.address_id) ? (
                        <span style={{ color: "var(--warn)", fontWeight: 600 }}>
                          two laws overlap
                        </span>
                      ) : (
                        "moves cleanly"
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}

          <OneBuilding
            test={test}
            result={result}
            address={pin ? byId.get(pin) : undefined}
            affected={new Set(result.affected_address_ids)}
            conflicted={conflicted}
          />
        </>
      )}

      <Sources result={result} />
    </>
  );
}

/** One building's answers on either side of the change.
 *
 *  This is the page's most specific claim — "this address was told one thing in
 *  December and another in January" — and it used to be two lines of
 *  `JSON.stringify`. Rendered rule by rule with the same badges the lookup
 *  uses, so an answer here and an answer there are read the same way.
 *
 *  Selection comes from the map or the table above, which is also what the old
 *  `?address_id=` box did one keystroke at a time.
 */
function OneBuilding({
  test,
  result,
  address,
  affected,
  conflicted,
}: {
  test: ChangeTest;
  result: ChangeTestResult;
  address: AddressRecord | undefined;
  affected: Set<string>;
  conflicted: Set<string>;
}) {
  if (!address) {
    return (
      <p className="case-note">
        Pick a building — on the map or in the table — to see what this case did
        to its answers.
      </p>
    );
  }

  const detail: ChangeDetail = result.detail ?? {};
  const sets = detail.rule_sets?.[address.address_id];
  const transition = detail.transitions?.[address.address_id];
  const ruleIds = [
    ...new Set([
      ...Object.keys(sets?.before ?? {}),
      ...Object.keys(sets?.after ?? {}),
      ...Object.keys(transition ?? {}),
    ]),
  ].sort();

  const inSet = affected.has(address.address_id);
  const before = detail.as_of_before ?? result.as_of;
  const after = detail.as_of_after ?? result.as_of;

  // A boundary or pending case keeps no before-and-after for a building —
  // there is no "before" in a question asked on one date. What it does know is
  // which of its rules claimed this address, which is the equivalent answer
  // and was previously printed only as a count somewhere above.
  const reachedBy = Object.entries(detail.per_rule ?? {})
    .filter(([, ids]) => ids.includes(address.address_id))
    .map(([canonicalId]) => canonicalId);

  return (
    <Card className="case-one">
      <p className="case-one-head">
        <span className="mono">{address.address_id}</span>
        <strong>{address.street_address}</strong>
        <span>
          {address.legal_city ?? address.postal_city}, {address.legal_state ?? address.state}
        </span>
      </p>

      <p className="case-one-verdict">
        {inSet
          ? test.type === "pending"
            ? "Would fall in scope if this bill passed. Nothing about its answers changes today."
            : test.type === "boundary"
              ? `Reached by one of the two city ordinances on ${result.as_of}.`
              : "One of the buildings whose answer this change moves."
          : "Outside this case's affected set."}
        {conflicted.has(address.address_id) &&
          " A local ban also covers it, so which law governs is for a person to settle."}
      </p>

      {ruleIds.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>Rule</Th>
              <Th>On {before}</Th>
              <Th>On {after}</Th>
            </tr>
          </thead>
          <tbody>
            {ruleIds.map((ruleId) => {
              const was = sets?.before?.[ruleId] ?? transition?.[ruleId]?.before;
              const now = sets?.after?.[ruleId] ?? transition?.[ruleId]?.after;
              const moved = was !== now;
              return (
                <tr key={ruleId} className={moved ? "picked-row" : undefined}>
                  <Td className="mono">{ruleId}</Td>
                  <Td>{was ? <Answer value={was} /> : <span className="case-absent">not reported</span>}</Td>
                  <Td>
                    {now ? <Answer value={now} /> : <span className="case-absent">not reported</span>}
                    {moved && <span className="case-moved">changed</span>}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      ) : reachedBy.length > 0 ? (
        <ul className="case-rules">
          {reachedBy.map((canonicalId) => (
            <li key={canonicalId}>
              <span className="mono">{canonicalId}</span>
              <span>
                {test.type === "pending"
                  ? "would cover this building if it passed"
                  : `covers this building on ${result.as_of}`}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="case-note">
          This case records no rule-by-rule answers for this building — it
          reports membership of the affected set only.
        </p>
      )}
    </Card>
  );
}

/** The law behind the case, quoted.
 *
 *  Folded into the case rather than kept in a separate mapping table at the
 *  foot of the page: a reader asking "what is the rule it actually read" is
 *  asking about the case in front of them, not about the id scheme.
 */
function Sources({ result }: { result: ChangeTestResult }) {
  const matches = result.canonical_matches ?? [];
  if (matches.length === 0) return null;
  return (
    <details className="case-sources">
      <summary>The law this case read</summary>
      <div>
        {matches.map((match) => (
          <div key={match.canonical_id} className="case-source">
            <p className="case-source-head">
              <span className="mono">{match.canonical_id}</span>
              <span>
                {match.matched
                  ? `read from ${match.matched_rule_ids.join(", ")}`
                  : "no extracted rule matched"}
              </span>
            </p>
            {match.note && <p className="case-note">{match.note}</p>}
            {match.sources.map((source) => (
              <div key={source.team_rule_id} className="case-quote">
                <blockquote>{source.quoted_span}</blockquote>
                <p>
                  <a href={source.source_url} target="_blank" rel="noreferrer">
                    {source.citation}
                  </a>
                  {source.source_doc_id && <span> · {source.source_doc_id}</span>}
                  <span> · retrieved {source.retrieved_at ?? "unknown"}</span>
                </p>
              </div>
            ))}
          </div>
        ))}
      </div>
    </details>
  );
}
