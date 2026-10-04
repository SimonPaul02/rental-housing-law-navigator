import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import {
  Card,
  Notice,
  PageHeading,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { AddressRecord, AddressStats } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AddressesPage() {
  // Signed in is the whole check here: this page serves public corpus material,
  // which reads the same to all four roles. What differs by role is which
  // questions the dashboard puts to it, not what the record says.
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const [addresses, stats] = await Promise.all([
    tryApi<AddressRecord[]>("/api/address-lookup/addresses?limit=100"),
    tryApi<AddressStats>("/api/address-lookup/stats"),
  ]);

  if (!addresses) {
    return <Notice title="Backend unreachable" tone="warning" />;
  }

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Module B"
        title="Address lookup"
        lede="The mailing city is not always the legal city. Jurisdiction is resolved against the Census incorporated-places layer before any rule is tested."
      />

      <StatStrip>
        <Stat label="Addresses" value={stats?.total ?? "—"} />
        <Stat
          label="Resolved"
          value={stats?.resolved ?? 0}
          sub={`${stats?.unresolved ?? 0} pending`}
        />
        <Stat
          label="City corrected"
          value={stats?.city_corrections ?? 0}
          sub="e.g. Dorchester → Boston"
        />
        <Stat
          label="No unit count"
          value={stats?.missing_units ?? 0}
          sub="coverage may be unknown"
          tone="warning"
        />
      </StatStrip>

      {stats?.resolved === 0 && (
        <Notice title="No jurisdictions resolved yet">
          Call <code className="mono">POST /api/address-lookup/resolve</code> to geocode a
          batch. Until then, lookups fall back to the mailing city, which is
          wrong for Boston neighbourhoods and San Ysidro.
        </Notice>
      )}

      <Card>
        <SectionTitle title="Sample addresses" hint="First 100 rows" />
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Address</Th>
              <Th>Postal city</Th>
              <Th>State</Th>
              <Th>Built</Th>
              <Th>Units</Th>
            </tr>
          </thead>
          <tbody>
            {addresses.map((a) => (
              <tr key={a.address_id}>
                <Td className="mono">{a.address_id}</Td>
                <Td>{a.street_address}</Td>
                <Td>{a.postal_city}</Td>
                <Td className="mono">{a.state}</Td>
                <Td className="tabular-nums">
                  {a.year_built ?? (
                    <span style={{ color: "var(--faint)" }}>—</span>
                  )}
                </Td>
                <Td className="tabular-nums">
                  {a.units ?? <span style={{ color: "var(--faint)" }}>—</span>}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
