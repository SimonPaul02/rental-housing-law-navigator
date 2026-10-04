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
import { categoryName, evaluate, fieldName, split } from "@/lib/lookups";
import { placePoint, unplaced } from "@/lib/points";
import { PLACES, ROLES } from "@/lib/roles";
import type {
  ChangeTestResult,
  Health,
  Place,
  RuleCheck,
  RuleOutcome,
  RuleRecord,
  RuleStats,
} from "@/lib/types";
import { ManageLink, NoRules, Source, WhereItIs } from "./shared";

/** The advocate's app: evidence, case by case.
 *
 * An advocate also holds many addresses, but unlike a provider's they do not
 * add up: each one is a different person's situation, so there is no roll-up
 * anywhere on this page and no count across cases — a "68% compliant
 * caseload" would be a number about nothing.
 *
 * What is tailored instead is depth of provenance. Every other view shows the
 * answer and the span behind it; this one also shows the checks the evaluator
 * ran to get there, because an advocate's use for an answer is to put it in a
 * letter and have it survive being argued with. The open conflicts come first
 * for the same reason: a rule two sources disagree about is one not to rely on
 * before a human has looked.
 */

const VOCAB = PLACES.advocate;

export async function AdvocateHome({ places, health }: { places: Place[]; health: Health }) {
  const [ruleStats, rules, changeResults] = await Promise.all([
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<RuleRecord[]>("/api/rule-extraction/rules?limit=2000"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
  ]);

  if (!ruleStats?.total) {
    return (
      <div className="space-y-8">
        <Intro health={health} count={places.length} />
        <NoRules />
      </div>
    );
  }

  const conflicts = (rules ?? []).filter((rule) => rule.conflict_flag);
  const lookups = await evaluate(places);
  const cases = places.map((place) => ({
    place,
    lookup: lookups.get(place.address_id),
    ...split(lookups.get(place.address_id)),
  }));

  // Two different things wear the word "conflict" in this system, and telling
  // them apart is this role's whole job:
  //
  //   a record conflict  two rules reach the same obligation and neither text
  //                      says which governs. A property of the corpus.
  //   a flag on an answer  something collided while answering *this* address -
  //                      most often sources that disagree about an effective
  //                      date, so whether the rule was in force on the query
  //                      date is unknown. A property of one answer.
  //
  // Reporting one number for both would be the sort of elision somebody would
  // later discover in front of a judge.
  const flagged = cases.flatMap((item) =>
    item.conflicts.map((outcome) => ({ place: item.place, outcome })),
  );
  const affected = new Map(
    (changeResults ?? []).flatMap((result) =>
      result.affected_address_ids.map((id) => [id, result] as const),
    ),
  );

  // One pin per matter, flagged where a conflict reaches it. Not a roll-up —
  // the map says where the caseload is, which is the one thing about a set of
  // unrelated cases that is fair to show together.
  const points = cases
    .map((item) =>
      placePoint(
        item.place,
        item.conflicts.length > 0 ? "conflict" : "applies",
        [
          `${item.applies.length} rules bind · ${item.unknown.length} unsettled`,
          item.conflicts.length > 0
            ? `${item.conflicts.length} flagged conflict${item.conflicts.length === 1 ? "" : "s"}`
            : "No flagged conflict",
          affected.has(item.place.address_id)
            ? `Moved by ${affected.get(item.place.address_id)!.test_id}`
            : "No change case touches it",
        ],
      ),
    )
    .filter((point): point is NonNullable<typeof point> => Boolean(point));
  const missingPins = unplaced(places);

  return (
    <div className="space-y-10">
      <Intro health={health} count={places.length} />

      <StatStrip>
        <Stat
          label="Open cases"
          value={places.length}
          sub="listed, never summed"
        />
        <Stat
          label="Flagged on your cases"
          value={flagged.length}
          sub="an answer the evaluator would not settle"
          tone={flagged.length ? "warning" : "good"}
        />
        <Stat
          label="Conflicts in the record"
          value={conflicts.length}
          sub="rules with no stated precedence"
          tone={conflicts.length ? "warning" : "good"}
        />
        <Stat
          label="Rules on record"
          value={ruleStats.total}
          sub={`from ${ruleStats.documents_extracted} of ${ruleStats.documents_with_text} documents`}
        />
      </StatStrip>

      {/* --------------------------------------- flags raised on these cases */}
      {flagged.length > 0 && (
        <section className="space-y-4">
          <SectionTitle
            title="Flagged while answering your cases"
            hint="Not a conflict between two rules — something the evaluator hit while answering this address, and refused to resolve for you. Most are sources that disagree about an effective date, which makes “was this in force on the query date” genuinely unknown rather than probably yes."
          />
          <ul className="space-y-3">
            {flagged.map(({ place, outcome }) => (
              <li
                key={`${place.id}-${outcome.team_rule_id}`}
                className="rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-medium">{place.label ?? place.street_address}</span>
                  <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                    {outcome.team_rule_id}
                  </span>
                  <span className="text-sm" style={{ color: "var(--muted)" }}>
                    {outcome.title ?? ""}
                  </span>
                  <ResultBadge result={outcome.result} />
                </div>
                <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
                  {outcome.explanation}
                </p>
                <Source outcome={outcome} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ----------------------------------------------- the open conflicts */}
      <section className="space-y-4">
        <SectionTitle
          title="Conflicts in the rule record"
          hint="Two rules reaching the same obligation with nothing in either text saying which governs. Nothing here is resolved automatically — the system reports the collision and stops."
          right={<ManageLink href="/rules" label="Every rule record" />}
        />
        {conflicts.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            No rule in the corpus is flagged as conflicting. That is an answer
            about the {ruleStats.total} records read so far, not a guarantee.
          </p>
        ) : (
          <ul className="space-y-3">
            {conflicts.map((rule) => (
              <li
                key={rule.team_rule_id}
                className="rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                    {rule.team_rule_id}
                  </span>
                  <span className="font-medium">{rule.title}</span>
                  <span className="text-xs" style={{ color: "var(--faint)" }}>
                    {rule.jurisdiction} · {categoryName(rule.category)}
                  </span>
                </div>
                {rule.conflict_note && (
                  <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
                    {rule.conflict_note}
                  </p>
                )}
                {rule.overrides.length > 0 && (
                  <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
                    Stated to override: <span className="mono">{rule.overrides.join(", ")}</span>
                  </p>
                )}
                <blockquote
                  className="mt-3 border-l-2 pl-3 text-sm italic"
                  style={{ borderColor: "var(--line)", color: "var(--faint)" }}
                >
                  &ldquo;{rule.quoted_span}&rdquo;
                </blockquote>
                <div className="mt-2 text-xs" style={{ color: "var(--faint)" }}>
                  <a
                    className="underline"
                    href={rule.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {rule.citation}
                  </a>
                  {rule.source_doc_id && ` · ${rule.source_doc_id}`}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ------------------------------------------------- where they are */}
      {points.length > 0 && (
        <MapPanel
          title="Where your cases are"
          hint="One pin per matter. Nothing is aggregated across them — this answers where, and only where."
          points={points}
          legend={["applies", "conflict"]}
          labels={points.length <= 12}
          height={380}
          footnote={
            missingPins > 0
              ? `${missingPins} of your ${places.length} cases could not be placed: the geocoder returned no coordinate for them.`
              : undefined
          }
          table={
            <Table>
              <thead>
                <tr>
                  <Th>Matter</Th>
                  <Th>Address</Th>
                  <Th>Legal city</Th>
                  <Th>Binding</Th>
                  <Th>Unsettled</Th>
                  <Th>Flagged</Th>
                  <Th>Change case</Th>
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={item.place.id}>
                    <Td>{item.place.label ?? "—"}</Td>
                    <Td>{item.place.street_address}</Td>
                    <Td>{item.place.legal_city ?? item.place.postal_city}</Td>
                    <Td className="tabular-nums">{item.applies.length}</Td>
                    <Td className="tabular-nums">{item.unknown.length}</Td>
                    <Td className="tabular-nums">
                      {item.conflicts.length || (
                        <span style={{ color: "var(--faint)" }}>—</span>
                      )}
                    </Td>
                    <Td className="mono text-xs">
                      {affected.get(item.place.address_id)?.test_id ?? "—"}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          }
        />
      )}

      {/* --------------------------------------------------------- the cases */}
      <section className="space-y-5">
        <SectionTitle
          title={places.length === 1 ? "Your case" : "Your cases"}
          hint="Each one on its own. No figure here is computed across them, because they are different people."
          right={<ManageLink label={`Edit ${VOCAB.many}`} />}
        />
        {places.length === 0 ? (
          <Notice title="No case yet">
            {VOCAB.empty}{" "}
            <Link href="/places" className="underline" style={{ color: "var(--accent)" }}>
              Add a case address
            </Link>
            .
          </Notice>
        ) : (
          cases.map((item) => (
            <Card key={item.place.id}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                <div>
                  <div className="text-lg font-medium">
                    {item.place.label ?? item.place.street_address}
                  </div>
                  {item.place.label && (
                    <div className="text-sm" style={{ color: "var(--muted)" }}>
                      {item.place.street_address}
                    </div>
                  )}
                  <div className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
                    <WhereItIs place={item.place} />
                  </div>
                  {item.place.note && (
                    <p className="mt-2 max-w-xl text-sm" style={{ color: "var(--faint)" }}>
                      {item.place.note}
                    </p>
                  )}
                </div>
                <div className="text-sm tabular-nums" style={{ color: "var(--muted)" }}>
                  {item.place.contract_count > 0 && (
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {item.place.contract_count}{" "}
                      {item.place.contract_count === 1 ? "agreement" : "agreements"} attached
                    </div>
                  )}
                  {item.applies.length} apply · {item.unknown.length} unsettled
                  {item.conflicts.length > 0 && (
                    <>
                      {" · "}
                      <span style={{ color: "var(--warn)" }}>
                        {item.conflicts.length} flagged while answering
                      </span>
                    </>
                  )}
                </div>
              </div>

              {affected.has(item.place.address_id) && (
                <p className="mt-3 text-sm" style={{ color: "var(--warn)" }}>
                  A change case moves an answer here:{" "}
                  <strong>{affected.get(item.place.address_id)!.title}</strong> (as
                  of {affected.get(item.place.address_id)!.as_of}).
                </p>
              )}

              {!item.lookup ? (
                <p className="mt-3 text-sm" style={{ color: "var(--faint)" }}>
                  Not evaluated — the API could not answer for this address just
                  now.
                </p>
              ) : (
                <div className="mt-4 space-y-4">
                  {item.applies.map((outcome) => (
                    <Evidence key={outcome.team_rule_id} outcome={outcome} />
                  ))}
                  {item.applies.length === 0 && (
                    <p className="text-sm" style={{ color: "var(--faint)" }}>
                      No rule in the corpus binds this address on{" "}
                      {health.default_as_of}.
                    </p>
                  )}
                  {item.unknown.length > 0 && (
                    <Expand
                      label={`${item.unknown.length} unsettled — each names the fact that would decide it`}
                    >
                      <div className="space-y-4">
                        {item.unknown.map((outcome) => (
                          <Evidence key={outcome.team_rule_id} outcome={outcome} />
                        ))}
                      </div>
                    </Expand>
                  )}
                  {item.superseded.length > 0 && (
                    <Expand label={`${item.superseded.length} superseded by another rule`}>
                      <ul className="space-y-2 text-sm">
                        {item.superseded.map((outcome) => (
                          <li key={outcome.team_rule_id}>
                            <span className="font-medium">
                              {outcome.title ?? outcome.team_rule_id}
                            </span>{" "}
                            <span style={{ color: "var(--muted)" }}>
                              — governed instead by{" "}
                              <span className="mono">{outcome.superseded_by}</span>.{" "}
                              {outcome.explanation}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </Expand>
                  )}
                </div>
              )}
            </Card>
          ))
        )}
      </section>
    </div>
  );
}

function Intro({ health, count }: { health: Health; count: number }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">
        {count === 0 ? "Evidence" : `Evidence for ${count} case${count === 1 ? "" : "s"}`}
      </h1>
      <p className="mt-2 max-w-2xl leading-relaxed" style={{ color: "var(--muted)" }}>
        {ROLES.advocate.cardinalityNote} Everything is answered as of{" "}
        <strong className="mono">{health.default_as_of}</strong>, and every answer
        carries the span it was read from and the checks that produced it.
      </p>
    </section>
  );
}

/** One rule against one address, with the reasoning exposed.
 *
 * The explanation is already built only from checks that actually ran, so it
 * cannot claim a reason the evaluator did not use. Showing the checks
 * underneath it is what lets somebody verify that, rather than take the
 * sentence on trust.
 */
function Evidence({ outcome }: { outcome: RuleOutcome }) {
  return (
    <div
      className="rounded-xl border p-4"
      style={{ borderColor: "var(--line)", background: "var(--surface)" }}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="mono text-xs" style={{ color: "var(--faint)" }}>
          {outcome.team_rule_id}
        </span>
        <span className="font-medium">{outcome.title ?? outcome.team_rule_id}</span>
        <ResultBadge result={outcome.result} />
        {outcome.jurisdiction && (
          <span className="text-xs" style={{ color: "var(--faint)" }}>
            {outcome.jurisdiction} · {categoryName(outcome.category)}
          </span>
        )}
        {outcome.conflict_flag && (
          <span className="text-xs font-semibold" style={{ color: "var(--warn)" }}>
            conflict flagged
          </span>
        )}
      </div>

      {outcome.key_value && (
        <div className="mt-1 text-sm font-medium" style={{ color: "var(--accent)" }}>
          {outcome.key_value}
        </div>
      )}

      <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
        {outcome.explanation}
      </p>

      {outcome.unresolved_fields.length > 0 && (
        <p className="mt-2 text-sm" style={{ color: "var(--warn)" }}>
          Blocked on: {outcome.unresolved_fields.map(fieldName).join(", ")}.
        </p>
      )}

      <Source outcome={outcome} />

      {outcome.checks.length > 0 && (
        <div className="mt-3">
          <Expand label={`How this was decided — ${outcome.checks.length} checks`}>
            <Checks checks={outcome.checks} />
          </Expand>
        </div>
      )}
    </div>
  );
}

function Checks({ checks }: { checks: RuleCheck[] }) {
  return (
    <Table>
      <thead>
        <tr>
          <Th>Check</Th>
          <Th>Verdict</Th>
          <Th>Fact read</Th>
          <Th>Why</Th>
        </tr>
      </thead>
      <tbody>
        {checks.map((check, index) => (
          <tr key={`${check.check}-${check.condition_id ?? index}`}>
            <Td className="whitespace-nowrap text-xs">{check.check}</Td>
            <Td className="whitespace-nowrap text-xs">
              <span
                style={{
                  color:
                    check.value === "true"
                      ? "var(--good)"
                      : check.value === "false"
                        ? "var(--faint)"
                        : "var(--warn)",
                  fontWeight: 600,
                }}
              >
                {check.value}
              </span>
            </Td>
            <Td className="text-xs">
              {check.field ? (
                <>
                  <span className="mono">{check.field}</span>
                  {check.fact_value !== null && check.fact_value !== undefined && (
                    <> = {String(check.fact_value)}</>
                  )}
                  {check.fact_status && check.fact_status !== "present" && (
                    <div style={{ color: "var(--warn)" }}>{check.fact_status}</div>
                  )}
                </>
              ) : (
                <span style={{ color: "var(--faint)" }}>—</span>
              )}
            </Td>
            <Td>
              <div style={{ maxWidth: "52ch" }}>{check.detail}</div>
              {check.source_span && (
                <div className="mt-1 text-xs italic" style={{ color: "var(--faint)" }}>
                  &ldquo;{check.source_span}&rdquo;
                </div>
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
