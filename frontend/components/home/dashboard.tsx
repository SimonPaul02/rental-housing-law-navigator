import { tryApi } from "@/lib/api";
import type { Health, Place } from "@/lib/types";
import type { Role } from "@/lib/roles";
import { AdvocateHome } from "./advocate";
import { AgencyHome } from "./agency";
import { ProviderHome } from "./provider";
import { RenterHome } from "./renter";
import { BackendDown } from "./shared";

/** The seam the four apps open along.
 *
 * This file used to *be* the dashboard — one page for all four roles, with its
 * two halves (your buildings, and the record) noted as the parts the tailored
 * views would be made of. They were, and this is now the dispatcher: the
 * health check happens once here, because all four need it and none of them
 * can say anything useful without it, and then the role picks the app.
 *
 * What differs between the four is not styling. It is how many addresses the
 * person has — see `Cardinality` in lib/roles.ts — which decides whether the
 * page is one building in depth, a portfolio in aggregate, a list of
 * independent cases, or the whole stock with no address of one's own at all.
 */

const DASHBOARDS: Record<
  Role,
  (props: { places: Place[]; health: Health }) => Promise<React.ReactElement>
> = {
  renter: RenterHome,
  provider: ProviderHome,
  agency: AgencyHome,
  advocate: AdvocateHome,
};

export async function Dashboard({ places, role }: { places: Place[]; role: Role }) {
  const health = await tryApi<Health>("/api/health");
  if (!health) return <BackendDown />;

  const View = DASHBOARDS[role];
  return <View places={places} health={health} />;
}
