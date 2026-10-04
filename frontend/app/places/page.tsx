import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { CONTRACTS, PLACES, ROLES } from "@/lib/roles";
import { Places } from "@/components/places";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import type { Contract, Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The addresses this person watches, named for what they are to them.
 *
 * One route, four pages. A renter has a home, a provider has buildings, an
 * advocate has cases and an agency has a shortlist it does not strictly need —
 * so the title, the explanation, the empty state and whether a name and note
 * are even asked for all come from the role's vocabulary rather than from a
 * shared noun that would have to fit all four badly.
 */
export default async function PlacesPage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A saved address" />;

  const vocab = PLACES[g.role];
  // Every agreement in one request and grouped in the browser, rather than one
  // request per building: a provider with eleven of them should not pay eleven
  // round trips to find out which have a lease on file.
  const [places, contracts] = await Promise.all([
    tryApi<Place[]>("/api/accounts/me/places"),
    tryApi<Contract[]>("/api/accounts/me/contracts"),
  ]);

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">{vocab.title}</h1>
        <p className="mt-2 max-w-2xl leading-relaxed" style={{ color: "var(--muted)" }}>
          {vocab.lede}
        </p>
        <p className="mt-2 max-w-2xl text-sm" style={{ color: "var(--faint)" }}>
          {ROLES[g.role].cardinalityNote}
        </p>
      </section>

      <Places
        initial={places ?? []}
        vocab={vocab}
        cardinality={ROLES[g.role].cardinality}
        contractVocab={CONTRACTS[g.role]}
        contracts={contracts ?? []}
      />
    </div>
  );
}
