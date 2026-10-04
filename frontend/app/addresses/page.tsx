import Link from "next/link";
import { gate } from "@/lib/auth";
import { Unavailable } from "@/components/gate-notice";
import { tryApi } from "@/lib/api";
import {
  Card,
  JurisdictionBadge,
  Notice,
  PageHeading,
  SectionTitle,
  Stat,
  StatStrip,
  Table,
  Td,
  Th,
  ZipDiscrepancyBadge,
} from "@/components/ui";
import type { AddressRecord, AddressStats } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AddressesPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  // Signed in is the whole check here: this page serves public corpus material,
  // which reads the same to all four roles. What differs by role is which
  // questions the dashboard puts to it, not what the record says.
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;

  const requestedStatus = (await searchParams).status;
  const status = requestedStatus === "resolved" || requestedStatus === "pending"
    ? requestedStatus
    : "all";
  const filter = status === "all" ? "" : `&resolved=${status === "resolved"}`;

  const [addresses, stats] = await Promise.all([
    tryApi<AddressRecord[]>(`/api/address-lookup/addresses?limit=500${filter}`),
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
        lede="See which legal jurisdictions are resolved and which addresses still need review. A ZIP discrepancy is shown separately because it does not change the legal city."
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
          batch. Until then, city rules remain uncertain; the mailing city is
          not treated as a verified legal city.
        </Notice>
      )}

      <Card>
        <SectionTitle
          title="Sample addresses"
          hint={`${addresses.length} shown · Resolved means legal jurisdiction verified; pending means not checked or needs review.`}
          right={
            <nav aria-label="Filter addresses by jurisdiction status" className="flex flex-wrap gap-2">
              {([
                ["all", "All", stats?.total],
                ["resolved", "Resolved", stats?.resolved],
                ["pending", "Pending", stats?.unresolved],
              ] as const).map(([value, label, count]) => (
                <Link
                  key={value}
                  href={value === "all" ? "/addresses" : `/addresses?status=${value}`}
                  aria-current={status === value ? "page" : undefined}
                  className={`badge ${status === value ? "address-filter-active" : ""}`}
                >
                  {label} {count ?? "—"}
                </Link>
              ))}
            </nav>
          }
        />
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Address</Th>
              <Th>Postal city</Th>
              <Th>Legal city</Th>
              <Th>Jurisdiction</Th>
              <Th>ZIP check</Th>
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
                <Td>{a.legal_city ?? "—"}</Td>
                <Td><JurisdictionBadge status={a.jurisdiction_status} /></Td>
                <Td>{a.zip_discrepancy ? <ZipDiscrepancyBadge /> : "—"}</Td>
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
            {addresses.length === 0 && (
              <tr>
                <td colSpan={9} className="px-5 py-8 text-center text-sm" style={{ color: "var(--muted)" }}>
                  No addresses in this view.
                </td>
              </tr>
            )}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
