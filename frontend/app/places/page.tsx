import { redirect } from "next/navigation";
import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { ROLES, contractsFor, placesFor } from "@/lib/roles";
import { Places } from "@/components/places";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import type { Contract, Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The addresses this person watches, named for what they are to them.
 *
 * One route, three pages — and for the fourth role, none. A renter has a home,
 * a provider has buildings and an advocate has cases, so the title, the
 * explanation, the empty state and whether a name and a note are even asked
 * for all come from the role's vocabulary rather than from a shared noun that
 * would have to fit them all badly.
 *
 * A housing agency has no vocabulary here because they hold no addresses of
 * their own: the whole sample is their subject, and the map on their overview
 * already carries every row a shortlist could have held. So they are sent
 * there rather than shown an empty list they have no reason to fill.
 */
export default async function PlacesPage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A saved address" />;

  const vocab = placesFor(g.role);
  const contractVocab = contractsFor(g.role);
  // Not a permission — there is nothing here this role may not read. There is
  // simply nothing here for them, so the redirect is the honest answer rather
  // than a page explaining its own emptiness.
  if (!vocab || !contractVocab) redirect("/home");

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
        contractVocab={contractVocab}
        contracts={contracts ?? []}
      />
    </div>
  );
}
