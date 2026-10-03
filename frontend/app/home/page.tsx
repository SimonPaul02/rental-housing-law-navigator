import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import { Dashboard } from "@/components/home/dashboard";
import type { Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** One dashboard, for all four roles.
 *
 * The role is resolved here and passed down even though the page does not yet
 * branch on it: it is the seam the four tailored views will open along, and
 * having it already in hand means the split is a change to one component
 * rather than to the routing.
 */
export default async function HomePage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A personal dashboard" />;

  const places = (await tryApi<Place[]>("/api/accounts/me/places")) ?? [];
  return <Dashboard places={places} roleLabel={g.account.role_label} />;
}
