import Link from "next/link";
import { Card, Notice, SectionTitle, Stat } from "@/components/ui";
import { Contracts } from "@/components/contracts";
import { MapPanel } from "@/components/map/map-panel";
import { Outcome } from "@/components/outcomes";
import { tryApi } from "@/lib/api";
import { answered, blocked } from "@/lib/changes";
import { money } from "@/lib/format";
import { categoryName, evaluate, fieldName, split } from "@/lib/lookups";
import { placePoint } from "@/lib/points";
import { CONTRACTS, PLACES, ROLES } from "@/lib/roles";
import type {
  ChangeTestResult,
  Contract,
  Health,
  Place,
  RuleOutcome,
  RuleStats,
} from "@/lib/types";
import { Facts, ManageLink, NoRules, PlaceBadges, WhereItIs } from "./shared";

/** The renter's app: one home, answered deeply.
 *
 * A renter has exactly one address that matters — the one they live in — so
 * this page is a single building rendered at full depth rather than a list
 * rendered shallowly. There is no roll-up, no coverage percentage and no
 * corpus statistic anywhere on it: how many documents the extractor read is
 * not a renter's question, and putting it here would only make the page look
 * like somebody else's dashboard.
 *
 * A second saved address is treated as what it almost always is — the flat
 * they are considering, or the one they just left — so it is listed as
 * somewhere they are also watching and never added to the first.
 */

const VOCAB = PLACES.renter;

export async function RenterHome({ places, health }: { places: Place[]; health: Health }) {
  const [ruleStats, changeResults, contracts] = await Promise.all([
    tryApi<RuleStats>("/api/rule-extraction/stats"),
    tryApi<ChangeTestResult[]>("/api/change-tracking/results"),
    tryApi<Contract[]>("/api/accounts/me/contracts"),
  ]);

  // The oldest saved address is the home. Nothing in the schema ranks them, and
  // inventing a "primary" flag for a list that is almost always one row long
  // would be a migration in exchange for nothing — so the rule is stated in
  // the copy instead, where a renter can act on it.
  const [home, ...watching] = places;

  if (!home) {
    return (
      <div className="space-y-8">
        <Intro health={health} />
        <Notice title="Tell us where you live">
          {VOCAB.empty}{" "}
          <Link href="/places" className="underline" style={{ color: "var(--accent)" }}>
            Find your building
          </Link>
          .
        </Notice>
      </div>
    );
  }

  const lookups = await evaluate(places);
  const lookup = lookups.get(home.address_id);
  const { applies, unknown, coming, superseded } = split(lookup);
  const rulesLoaded = Boolean(ruleStats?.total);
  const lease = (contracts ?? []).filter((contract) => contract.place_id === home.id);
  const point = placePoint(home, "mine", [
    lease.length
      ? `${lease.length} ${lease.length === 1 ? "lease" : "leases"} on file`
      : "No lease on file",
  ]);

  // The rent a person typed in, next to the rule that governs what it may
  // become. Deliberately side by side and never arithmetic: the cap is often
  // "the lower of 5% + CPI or 10%", the CPI figure is not in this corpus, and
  // a number produced here would be a number somebody acted on.
  const rentRules = applies.filter(
    (outcome) => outcome.key_value && /rent/i.test(outcome.category ?? ""),
  );
  const rent = lease.find((contract) => contract.monthly_rent_cents !== null);

  // Only the cases that move an answer at this address. A renter has no use for
  // the other four. A case that could not be computed is neither: it is said
  // out loud, so "nothing changed for you" never rests on a case that did not run.
  const mine = answered(changeResults).filter((result) =>
    result.affected_address_ids.includes(home.address_id),
  );
  const notComputed = blocked(changeResults);

  return (
    <div className="space-y-10">
      <Intro health={health} />

      {/* ------------------------------------------------------- the home */}
      {point && (
        <MapPanel
          points={[point]}
          legend={["mine"]}
          height={260}
          labels
          footnote="Your building, at the coordinate the Census geocoder returned for it — the same answer that settled which city's rules reach you."
        />
      )}

      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <div>
            <div className="text-lg font-medium">{home.street_address}</div>
            <div className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
              <WhereItIs place={home} />
            </div>
            <PlaceBadges place={home} />
          </div>
          <div className="text-sm">
            <Facts place={home} />
          </div>
        </div>
      </Card>

      {/* ------------------------------------------------------ the lease */}
      <Card>
        <Contracts
          placeId={home.id}
          units={home.units}
          vocab={CONTRACTS.renter}
          initial={lease}
        />
        {rent && rentRules.length > 0 && (
          <div className="mt-5 rounded-xl border p-4" style={{ borderColor: "var(--line)" }}>
            <div className="text-sm">
              You have recorded a rent of{" "}
              <strong>{money(rent.monthly_rent_cents as number)} a month</strong>.
              {rentRules.length === 1 ? " The rule" : " The rules"} that
              {rentRules.length === 1 ? " governs" : " govern"} increases here
              {rentRules.length === 1 ? " says" : " say"}:
            </div>
            <ul className="mt-2 space-y-2 text-sm">
              {rentRules.map((outcome) => (
                <li key={outcome.team_rule_id}>
                  <strong style={{ color: "var(--accent)" }}>{outcome.key_value}</strong>{" "}
                  <span style={{ color: "var(--muted)" }}>
                    — {outcome.title ?? outcome.team_rule_id}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs" style={{ color: "var(--faint)" }}>
              Shown side by side, not multiplied out. A cap worded as &ldquo;the
              lower of 5% + CPI or 10%&rdquo; needs a CPI figure this corpus does
              not carry, and a number produced here would be one you acted on.
            </p>
          </div>
        )}
      </Card>

      {!rulesLoaded ? (
        <NoRules />
      ) : !lookup ? (
        <Notice title="This building has not been evaluated yet" tone="warning">
          The API could not answer for it just now. Nothing is wrong with your
          address — try again in a moment.
        </Notice>
      ) : (
        <>
          {home.jurisdiction_status !== "resolved" && (
            <Notice title="Which city's rules reach you is not settled yet" tone="warning">
              Your building&rsquo;s legal city has not been verified, so anything
              a city sets — rent caps, notice periods — is still uncertain. State
              law below is unaffected.
            </Notice>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Stat
              label="Protections that apply to you"
              value={applies.length}
              sub="on today's date"
              tone={applies.length ? "good" : undefined}
            />
            <Stat
              label="Questions we cannot answer yet"
              value={unknown.length}
              sub="each one names the missing fact"
              tone={unknown.length ? "warning" : undefined}
            />
          </div>

          {/* --------------------------------------- what applies, by kind */}
          <section className="space-y-5">
            <SectionTitle
              title="What the law gives you here"
              hint="Grouped by the kind of thing it covers. Every one quotes the text it came from, so you can check it rather than take our word for it."
            />
            {applies.length === 0 ? (
              <p className="text-sm" style={{ color: "var(--faint)" }}>
                {unknown.length + coming.length + superseded.length > 0
                  ? "No rule is confirmed to apply on this date. The other outcomes below explain what remains unsettled, is not yet in force, or is superseded."
                  : "No rule in this corpus covers your building on today's date. That is an answer about the rules we have read, not a statement that nothing protects you."}
              </p>
            ) : (
              <ByCategory outcomes={applies} />
            )}
          </section>

          {/* -------------------------------------------- what is pending */}
          {coming.length > 0 && (
            <section className="space-y-4">
              <SectionTitle
                title="Not in force yet"
                hint="Passed or proposed, with a date in the future — or still waiting to be enacted."
              />
              <ul className="space-y-3">
                {coming.map((outcome) => (
                  <Outcome key={outcome.team_rule_id} outcome={outcome} />
                ))}
              </ul>
            </section>
          )}

          {superseded.length > 0 && (
            <section className="space-y-4">
              <SectionTitle
                title="Governed by another rule here"
                hint="These rules were checked, but a reviewed relationship gives another applicable rule precedence for this building."
              />
              <ul className="space-y-3">
                {superseded.map((outcome) => (
                  <Outcome key={outcome.team_rule_id} outcome={outcome} />
                ))}
              </ul>
            </section>
          )}

          {/* ------------------------------------------- what is blocked */}
          {unknown.length > 0 && (
            <section className="space-y-4">
              <SectionTitle
                title="What we cannot answer yet, and why"
                hint="Not a guess either way. Where a rule turns on a fact the public record does not carry, the honest answer is that it is unsettled — and the fact is named so you know what to ask for."
              />
              <ul
                className="space-y-2 rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                {unknown.map((outcome) => (
                  <li key={outcome.team_rule_id} className="text-sm">
                    <strong className="font-medium">
                      {outcome.title ?? outcome.team_rule_id}
                    </strong>
                    {outcome.unresolved_fields.length > 0 && (
                      <span style={{ color: "var(--warn)" }}>
                        {" "}
                        — needs {outcome.unresolved_fields.map(fieldName).join(" and ")}
                      </span>
                    )}
                    <div className="mt-0.5" style={{ color: "var(--muted)" }}>
                      {outcome.explanation}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {/* ----------------------------------------------------- the changes */}
      <section className="space-y-4">
        <SectionTitle
          title="Has anything changed for you?"
          hint="The supplied law-change cases, answered by re-running the same evaluator at the relevant dates."
          right={<ManageLink href="/changes" label="All change cases" />}
        />
        {mine.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            None of the change cases
            {notComputed.length > 0 ? " that could be checked" : ""} moves an answer
            at your address.
          </p>
        ) : (
          <ul className="space-y-3">
            {mine.map((result) => (
              <Card key={result.test_id}>
                <div className="font-medium">{result.title}</div>
                <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
                  {result.notes}
                </p>
                <div className="mt-2 text-xs mono" style={{ color: "var(--faint)" }}>
                  evaluated as of {result.as_of}
                </div>
              </Card>
            ))}
          </ul>
        )}
        {notComputed.length > 0 && (
          <p className="text-xs" style={{ color: "var(--faint)" }}>
            {notComputed.length} of the change cases could not be checked yet, so
            this list may not be complete.
          </p>
        )}
      </section>

      {/* ------------------------------------------------- also watching */}
      {watching.length > 0 && (
        <section className="space-y-3">
          <SectionTitle
            title="Also watching"
            hint="Kept, but not answered in full here — this page is about the home you live in."
            right={<ManageLink label="Edit addresses" />}
          />
          <ul className="space-y-2">
            {watching.map((place) => {
              const other = split(lookups.get(place.address_id));
              return (
                <li
                  key={place.id}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border p-3 text-sm"
                  style={{ borderColor: "var(--line)", background: "var(--surface)" }}
                >
                  <span className="min-w-48 flex-1">
                    <span className="font-medium">{place.street_address}</span>
                    <span className="ml-2" style={{ color: "var(--muted)" }}>
                      <WhereItIs place={place} />
                    </span>
                  </span>
                  <span className="tabular-nums" style={{ color: "var(--muted)" }}>
                    {other.applies.length} apply · {other.unknown.length} unsettled
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function Intro({ health }: { health: Health }) {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">Your home, and what covers it</h1>
      <p className="mt-2 max-w-2xl leading-relaxed" style={{ color: "var(--muted)" }}>
        {ROLES.renter.cardinalityNote} Answers are for{" "}
        <strong className="mono">{health.default_as_of}</strong>, because a rule
        that was not in force last year and is now has to be asked on a date.
      </p>
    </section>
  );
}

/** The rules that apply, grouped by the kind of thing they cover.
 *
 * Twelve cards in a flat list is a wall; the same twelve under "rent increases",
 * "notice to leave" and "deposits" is something a person can read looking for
 * one answer, which is how a renter arrives.
 */
function ByCategory({ outcomes }: { outcomes: RuleOutcome[] }) {
  const groups = new Map<string, RuleOutcome[]>();
  for (const outcome of outcomes) {
    const key = outcome.category ?? "uncategorised";
    groups.set(key, [...(groups.get(key) ?? []), outcome]);
  }
  return (
    <div className="space-y-7">
      {[...groups.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([category, items]) => (
          <div key={category}>
            <h3
              className="mb-2 text-xs font-semibold uppercase tracking-wider"
              style={{ color: "var(--faint)" }}
            >
              {categoryName(category)}
            </h3>
            <ul className="space-y-3">
              {items.map((outcome) => (
                <Outcome key={outcome.team_rule_id} outcome={outcome} />
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}
