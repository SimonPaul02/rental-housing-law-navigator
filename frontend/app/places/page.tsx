import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { PLACE } from "@/lib/roles";
import { Places } from "@/components/places";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import type { Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The buildings this person watches.
 *
 * The same list and the same name for every role at the moment. When the four
 * apps diverge this is where the noun changes - a renter has a home, a
 * provider has properties, an advocate has case addresses - and nothing else
 * about the page has to.
 */
export default async function PlacesPage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A saved address" />;

  const places = (await tryApi<Place[]>("/api/accounts/me/places")) ?? [];

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">{PLACE.add}</h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          Private to you. Nothing in this app can address another account, so
          there is nobody to share a {PLACE.one} with and no one who can see it.
        </p>
      </section>

      <Places initial={places} noun={PLACE} />
    </div>
  );
}
