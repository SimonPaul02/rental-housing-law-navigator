import { Suspense } from "react";
import { tryApi } from "@/lib/api";
import type { Health, Place } from "@/lib/types";
import type { Role } from "@/lib/roles";
import { Assistant } from "@/components/assistant/chat";
import { AdvocateHome } from "./advocate";
import { AgencyHome } from "./agency";
import { ProviderHome } from "./provider";
import { RenterHome } from "./renter";
import { BackendDown } from "./shared";

/** The seam the four apps open along — and now the one place the agent sits.
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
 *
 * The assistant is above all four rather than inside any of them, for the
 * same reason the health check is: all four want it, and what makes it
 * role-specific is already role-specific one level down — the agent's own
 * prompt and its roster of tools, which an agency's and a renter's do not
 * share. One mounting point here means there is no fifth place for the four to
 * drift apart.
 *
 * It is first on the page because it is the way in: a question is a shorter
 * path to an answer than reading a dashboard and working out which panel
 * holds it. What follows it is the record the answer came out of, which is
 * still worth having laid out — an agent is a good way to ask and a poor way
 * to scan five hundred rows.
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
  return (
    <div className="space-y-10">
      {/* The agent is a client component and paints at once. The record below
          is server-rendered, and for a portfolio of five hundred buildings it
          is ten seconds of arithmetic the first time — so it streams in behind
          its own boundary rather than holding the page. Asking a question is
          the thing somebody came to do; it should never wait on a roll-up. */}
      <Assistant role={role} />
      <div className="record-divider">
        <h2>The record behind it</h2>
        <p>
          Everything the assistant answers from, laid out. It is the same data,
          and the same figures — nothing on this page is produced by a model.
        </p>
      </div>
      <Suspense fallback={<RecordLoading />}>
        <View places={places} health={health} />
      </Suspense>
    </div>
  );
}

function RecordLoading() {
  return (
    <p className="text-sm" style={{ color: "var(--faint)" }}>
      Working through the buildings on this account…
    </p>
  );
}
