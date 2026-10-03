import Link from "next/link";
import { tryApi } from "@/lib/api";
import { Card, Notice, SectionTitle, Stat } from "@/components/ui";
import { Outcomes } from "@/components/outcomes";
import type { ChangeTestResult, LookupResponse, Place } from "@/lib/types";

/** A renter's app: one address, and what the law says about it.
 *
 * Everything else this system can do - the corpus, the 500-row sample, the
 * change harness - is beside the point to somebody who wants to know whether
 * their own rent increase was legal. So the view is one building deep, and the
 * three things it shows are: what applies, what is about to change, and what
 * cannot be answered because the public record is missing a fact.
 */
export async function RenterHome({ places }: { places: Place[] }) {
  const home = places[0];

  if (!home) {
    return (
      <div className="space-y-6">
        <Header />
        <Notice title="Set your address first">
          Tell us where you live and this page fills in: which rules cover your
          building, what each one entitles you to, and the words of the law it
          comes from.{" "}
          <Link href="/places" className="underline" style={{ color: "var(--accent)" }}>
            Set your address →
          </Link>
        </Notice>
      </div>
    );
  }

  const lookup = await tryApi<LookupResponse>(
    `/api/address-lookup/lookup/${home.address_id}`,
  );
  const changes = (await tryApi<ChangeTestResult[]>("/api/change-tracking/results")) ?? [];

  if (!lookup) {
    return (
      <div className="space-y-6">
        <Header />
        <Notice title="No answer for your address yet" tone="warning">
          The rule set has not been read in yet, or the API is still starting.
          Nothing is wrong with your address.
        </Notice>
      </div>
    );
  }

  const applies = lookup.outcomes.filter((o) => o.result === "applies");
  const unknown = lookup.outcomes.filter((o) => o.result === "unknown");
  // A change case matters to a renter only if it touches their own building.
  const mine = changes.filter((c) => c.affected_address_ids.includes(home.address_id));

  return (
    <div className="space-y-8">
      <Header />

      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div>
            <div className="text-lg font-medium">{home.street_address}</div>
            <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {lookup.legal_city ?? home.postal_city}, {lookup.legal_state ?? home.state}
              {home.postal_city_differs && (
                <>
                  {" — your mail says "}
                  <strong>{home.postal_city}</strong>, but the rules that cover
                  you are {lookup.legal_city}&rsquo;s.
                </>
              )}
            </div>
          </div>
          <Link href="/places" className="text-sm underline" style={{ color: "var(--accent)" }}>
            Change address
          </Link>
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label="Rules protecting you"
          value={applies.length}
          tone={applies.length ? "good" : undefined}
        />
        <Stat
          label="Depends on a missing fact"
          value={unknown.length}
          sub="see below"
          tone={unknown.length ? "warning" : undefined}
        />
        <Stat label="Changing for you" value={mine.length} sub={`as of ${lookup.as_of}`} />
      </div>

      <section>
        <SectionTitle
          title="What applies to your home"
          hint="Each one quotes the law it comes from, so you can check it rather than take our word for it."
        />
        <Outcomes
          outcomes={applies}
          empty="No rule in the corpus covers this building on this date."
        />
      </section>

      {mine.length > 0 && (
        <section>
          <SectionTitle
            title="Changing for you"
            hint="A law change in the supplied cases that moves the answer at your address."
          />
          <ul className="space-y-3">
            {mine.map((change) => (
              <Card key={change.test_id}>
                <div className="font-medium">{change.title}</div>
                <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
                  {change.notes}
                </p>
              </Card>
            ))}
          </ul>
        </section>
      )}

      {unknown.length > 0 && (
        <section>
          <SectionTitle
            title="Cannot be answered yet"
            hint="Not a guess either way. Each of these turns on a fact the public record does not carry for your building — the one named is what would settle it."
          />
          <Outcomes outcomes={unknown} empty="" />
        </section>
      )}
    </div>
  );
}

function Header() {
  return (
    <section>
      <h1 className="text-2xl font-semibold tracking-tight">Your home</h1>
      <p
        className="mt-2 max-w-2xl leading-relaxed"
        style={{ color: "var(--text-secondary)" }}
      >
        Which housing rules cover the building you live in, and what each one
        entitles you to. Not legal advice — but every line here points at the
        text it came from.
      </p>
    </section>
  );
}
