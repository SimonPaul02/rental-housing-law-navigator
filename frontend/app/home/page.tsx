import { gate } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";
import { RenterHome } from "@/components/home/renter";
import { ProviderHome } from "@/components/home/provider";
import { AgencyHome } from "@/components/home/agency";
import { AdvocateHome } from "@/components/home/advocate";
import type { Place } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Four apps behind one path.
 *
 * The role is resolved on the server, so the dashboard a person gets is the
 * only one their browser is ever sent - a renter never receives an agency's
 * markup and has nothing to toggle.
 *
 * Renter and provider are about particular buildings, so they need the saved
 * places; agency and advocate are about the record as a whole and do not, so
 * they are not made to wait for a query they will not read.
 */
export default async function HomePage() {
  const g = await gate("/home");
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="A personal dashboard" />;

  switch (g.role) {
    case "renter":
    case "provider": {
      const places = (await tryApi<Place[]>("/api/accounts/me/places")) ?? [];
      return g.role === "renter" ? (
        <RenterHome places={places} />
      ) : (
        <ProviderHome places={places} />
      );
    }
    case "agency":
      return <AgencyHome />;
    case "advocate":
      return <AdvocateHome />;
  }
}
