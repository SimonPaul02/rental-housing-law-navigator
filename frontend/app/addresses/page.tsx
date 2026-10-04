import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import { AddressExplorer } from "@/components/explorer/address-explorer";
import { placesFor } from "@/lib/roles";
import { Notice, PageHeading, Stat, StatStrip } from "@/components/ui";
import type { AddressRecord, AddressStats } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Module B's own view of the sample: where the addresses are, and which of
 *  them the system can actually answer for.
 *
 *  The whole sample is fetched in one request and narrowed in the browser.
 *  Five hundred rows is what the sample is and what the API caps a page at, and
 *  filtering locally is what lets a map and a table stay in step with a slider
 *  as it moves — see components/explorer/filters.ts for the trade.
 *
 *  Signed in is the whole check: this page serves public corpus material, which
 *  reads the same to all four roles. What differs by role is which questions
 *  the dashboard puts to it, not what the record says.
 */
export default async function AddressesPage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const [addresses, stats] = await Promise.all([
    tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=500"),
    tryApi<AddressStats>("/api/address-lookup/stats"),
  ]);

  if (!addresses) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  const placed = stats?.with_coordinates ?? 0;

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Module B"
        title="Address lookup"
        lede="Which legal jurisdiction each address resolves to, and which records still need review. A ZIP discrepancy is tracked separately because it does not change the legal city."
      />

      <StatStrip>
        <Stat label="Addresses" value={stats?.total ?? addresses.length} />
        <Stat
          label="Jurisdiction verified"
          value={stats?.resolved ?? 0}
          sub={`${stats?.unresolved ?? 0} need review`}
        />
        <Stat
          label="Mailing city corrected"
          value={stats?.city_corrections ?? 0}
          sub="e.g. Dorchester → Boston"
        />
        <Stat
          label="Can be mapped"
          value={placed}
          sub={`${(stats?.total ?? 0) - placed} have no coordinate`}
          tone={placed < (stats?.total ?? 0) ? "warning" : "good"}
        />
      </StatStrip>

      {stats?.resolved === 0 && (
        <Notice title="No jurisdictions resolved yet">
          Call <code className="mono">POST /api/address-lookup/resolve</code> to geocode a
          batch. Until then, city rules remain uncertain; the mailing city is not
          treated as a verified legal city, and there are no coordinates to map.
        </Notice>
      )}

      {/* Saving is offered only to the roles that have somewhere to save to.
          An agency keeps no addresses of their own, and a button that filed a
          row into a list they can never open would be worse than no button. */}
      <AddressExplorer
        addresses={addresses}
        stats={stats}
        canSave={g.mode === "account" && Boolean(placesFor(g.role))}
      />
    </div>
  );
}
