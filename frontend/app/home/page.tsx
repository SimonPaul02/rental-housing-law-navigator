import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import { Dashboard } from "@/components/home/dashboard";
import type { Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Four roles, four dashboards.
 *
 * The role is resolved here and handed to one component, which dispatches. The
 * routing does not branch: all four apps live at /home, because a person has
 * one role at a time and switching it in settings should change what this page
 * is, not where they are.
 */
export default async function HomePage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A personal dashboard" />;

  const places = (await tryApi<Place[]>("/api/accounts/me/places")) ?? [];
  return <Dashboard places={places} role={g.role} />;
}
