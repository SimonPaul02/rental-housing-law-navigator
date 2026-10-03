import Link from "next/link";
import { tryApi } from "@/lib/api";
import { Card, Notice, SectionTitle, Stat } from "@/components/ui";
import { Outcomes } from "@/components/outcomes";
import type { LookupResponse, Place, RuleOutcome } from "@/lib/types";

/** A housing provider's app: obligations per building, and what is unsettled.
 *
 * The renter's view and this one read the same evaluation and mean opposite
 * things by it - "this protects you" is "this binds you" - so the framing is
 * the whole difference. What is genuinely different is the third column: for a
 * provider an "unknown" is a task, because it names the one fact that would
 * settle the matter, and that fact is usually one they hold.
 */
const MAX_BUILDINGS = 8;

export async function ProviderHome({ places }: { places: Place[] }) {
  if (places.length === 0) {
    return (
      <div className="space-y-6">
        <Header />
        <Notice title="Add a property first">
          Each one gets its own list: the rules it is subject to, the exemptions
          it can claim, and the facts still missing before an answer is
          possible.{" "}
          <Link href="/places" className="underline" style={{ color: "var(--accent)" }}>
            Add a property →
          </Link>
        </Notice>
      </div>
    );
  }

  const shown = places.slice(0, MAX_BUILDINGS);
  // One request per building, in parallel - each is an independent evaluation.
  const lookups = await Promise.all(
    shown.map((place) =>
      tryApi<LookupResponse>(`/api/address-lookup/lookup/${place.address_id}`),
    ),
  );

  const binding = (outcomes: RuleOutcome[]) =>
    outcomes.filter((o) => o.result === "applies");
  const unsettled = (outcomes: RuleOutcome[]) =>
    outcomes.filter((o) => o.result === "unknown");

  const allOutcomes = lookups.flatMap((l) => l?.outcomes ?? []);
  const totalBinding = binding(allOutcomes).length;
  const totalUnsettled = unsettled(allOutcomes).length;

  return (
    <div className="space-y-8">
      <Header />

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Properties" value={places.length} />
        <Stat
          label="Obligations in force"
          value={totalBinding}
          sub="across all properties"
        />
        <Stat
          label="Awaiting a fact"
          value={totalUnsettled}
          sub="year built or unit count"
          tone={totalUnsettled ? "warning" : undefined}
        />
      </div>

      {places.length > MAX_BUILDINGS && (
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Showing the first {MAX_BUILDINGS} of {places.length}.
        </p>
      )}

      {shown.map((place, index) => {
        const lookup = lookups[index];
        const bind = lookup ? binding(lookup.outcomes) : [];
        const open = lookup ? unsettled(lookup.outcomes) : [];
        return (
          <section key={place.id} className="space-y-4">
            <Card>
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <div>
                  <div className="text-lg font-medium">
                    {place.label ?? place.street_address}
                  </div>
                  <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
                    {place.legal_city ?? place.postal_city},{" "}
                    {place.legal_state ?? place.state}
                    {place.postal_city_differs && (
                      <>
                        {" · legal city is "}
                        <strong>{place.legal_city}</strong>, not {place.postal_city}
                      </>
                    )}
                  </div>
                </div>
                <div className="text-sm tabular-nums" style={{ color: "var(--text-muted)" }}>
                  {place.year_built ? `built ${place.year_built}` : "year built unknown"}
                  {" · "}
                  {place.units ? `${place.units} units` : "unit count unknown"}
                </div>
              </div>
            </Card>

            {!lookup ? (
              <Notice title="No evaluation for this building yet" tone="warning">
                The rule set has not been read in yet, or the API is still
                starting.
              </Notice>
            ) : (
              <>
                <SectionTitle
                  title="What binds this building"
                  hint={`${bind.length} in force on ${lookup.as_of}`}
                />
                <Outcomes
                  outcomes={bind}
                  empty="No rule in the corpus covers this building on this date."
                />
                {open.length > 0 && (
                  <>
                    <SectionTitle
                      title="Unsettled"
                      hint="Each names the fact that would decide it — usually one you hold."
                    />
                    <Outcomes outcomes={open} empty="" />
                  </>
                )}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

function Header() {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">Your properties</h1>
      <p
        className="mt-2 max-w-2xl leading-relaxed"
        style={{ color: "var(--text-secondary)" }}
      >
        What each building is subject to, which exemptions it can claim, and
        which facts are still missing before an answer is possible. An exemption
        that matches means the rule does <em>not</em> apply — coverage and
        exemptions are evaluated separately, because they pull in opposite
        directions.
      </p>
    </section>
  );
}
