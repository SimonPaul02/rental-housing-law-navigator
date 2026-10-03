import { gate, roleSpec } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { Places } from "@/components/places";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import type { Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The buildings this person watches.
 *
 * The same list for every role, named differently: a renter has a home, a
 * provider has properties, an advocate has case addresses. The noun comes from
 * lib/roles.ts rather than from four copies of this page, because what differs
 * is only what it is called.
 */
export default async function PlacesPage() {
  const g = await gate("/places");
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A saved address" />;

  const spec = roleSpec(g.role);
  const places = (await tryApi<Place[]>("/api/accounts/me/places")) ?? [];

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">{spec.place.add}</h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          Private to you. Nothing in this app can address another account, so
          there is nobody to share a {spec.place.one} with and no one who can
          see it.
        </p>
      </section>

      <Places initial={places} noun={spec.place} />
    </div>
  );
}
