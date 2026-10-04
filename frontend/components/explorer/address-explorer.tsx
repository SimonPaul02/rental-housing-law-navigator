"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { LazyAddressMap as AddressMap } from "@/components/map/lazy-map";
import { ViewToggle, type View } from "@/components/view-toggle";
import {
  JurisdictionBadge,
  SectionTitle,
  Table,
  Td,
  Th,
  ZipDiscrepancyBadge,
} from "@/components/ui";
import { clientApi } from "@/lib/client-api";
import type { AddressRecord, AddressStats } from "@/lib/types";
import type { PointTone } from "@/components/map/model";
import {
  EMPTY,
  cityCounts,
  isActive,
  matches,
  placeable,
  toCsv,
  toPoint,
  type Filters,
} from "./filters";

/** The sample, explorable: filter it, see it on a map, read it in a table.
 *
 * One component serves the agency's five hundred rows and a provider looking
 * for their next building, because the question underneath is the same one —
 * *which addresses, and where* — and the difference between those two people
 * is which answer they then act on.
 *
 * The two views are never two datasets. Whatever the filters leave is what the
 * map draws and what the table lists, down to the row; the toggle is a
 * rendering choice, so nothing can hide in one view and not the other. The
 * table is also the view that works with a screen reader and with the
 * browser's own find, which is why it is always one click away and never
 * replaced.
 *
 * Jurisdictions behave differently from every other filter on purpose. Picking
 * Los Angeles *highlights* it — a ring on its pins and its extent shaded —
 * while leaving everything else on screen, because the useful question is
 * usually "where is LA relative to the rest of this" rather than "hide the
 * rest". One checkbox turns the highlight into a filter when it is the other
 * question.
 */

const MAX_CITY_CHIPS = 8;

export function AddressExplorer({
  addresses,
  stats,
  canSave = false,
  initialView = "map",
  heading = "Sample addresses",
  hint,
}: {
  addresses: AddressRecord[];
  stats: AddressStats | null;
  /** Whether the selected address can be added to the viewer's own list. */
  canSave?: boolean;
  initialView?: View;
  heading?: string;
  hint?: string;
}) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [more, setMore] = useState(false);
  const [allCities, setAllCities] = useState(false);
  const [view, setView] = useState<View>(initialView);
  const [selected, setSelected] = useState<string | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((current) => ({ ...current, [key]: value }));

  const shown = useMemo(
    () => addresses.filter((address) => matches(address, filters)),
    [addresses, filters],
  );
  const points = useMemo(
    () => shown.map((address) => toPoint(address)).filter((p): p is NonNullable<typeof p> => !!p),
    [shown],
  );
  const facets = useMemo(() => cityCounts(addresses, filters), [addresses, filters]);
  const unplaceable = shown.length - points.length;
  const picked = shown.find((address) => address.address_id === selected) ?? null;

  const cityChips = useMemo(() => {
    if (allCities) return facets;
    const top = facets.slice(0, MAX_CITY_CHIPS);
    // A selected city always has a chip, even if it has dropped out of the top
    // few — otherwise deselecting it means hunting through the long list.
    const extra = facets.filter(
      (f) => filters.cities.includes(f.city) && !top.some((t) => t.city === f.city),
    );
    return [...top, ...extra];
  }, [facets, filters.cities, allCities]);

  /** How much is set behind the disclosure, so the button can say so.
   *
   * A collapsed panel that is quietly filtering is the worst version of this
   * pattern: somebody sees 48 of 500 rows and no reason why. The count is what
   * keeps it honest. */
  const advanced =
    (filters.builtFrom ? 1 : 0) +
    (filters.builtTo ? 1 : 0) +
    (filters.unitsFrom ? 1 : 0) +
    (filters.unitsTo ? 1 : 0) +
    (filters.zipOnly ? 1 : 0) +
    (filters.missingYearBuilt ? 1 : 0) +
    (filters.missingUnits ? 1 : 0) +
    (filters.mappableOnly ? 1 : 0) +
    (filters.unplaceableOnly ? 1 : 0);

  function toggleCity(city: string) {
    setFilters((current) => ({
      ...current,
      cities: current.cities.includes(city)
        ? current.cities.filter((name) => name !== city)
        : [...current.cities, city],
    }));
  }

  /** The two coordinate filters are each other's opposite, so only one holds.
   *
   * Both on at once asks for the rows that have a coordinate and have none,
   * which is always nothing — a filter state whose only possible answer is an
   * empty table is a trap, not a choice. */
  function setCoordinate(key: "mappableOnly" | "unplaceableOnly", on: boolean) {
    setFilters((current) => ({
      ...current,
      mappableOnly: key === "mappableOnly" && on,
      unplaceableOnly: key === "unplaceableOnly" && on,
    }));
  }

  /** Jump from the warning count to the rows it is counting.
   *
   * A number somebody cannot click is a dead end: told that 26 addresses are
   * missing from the map, the next question is always *which* 26, usually
   * because somebody means to go and geocode them. The jump switches to the
   * table as well as filtering, because these rows are defined by having no
   * pin — narrowing the map to them would draw an empty map. */
  function listUnplaceable() {
    const on = !filters.unplaceableOnly;
    setCoordinate("unplaceableOnly", on);
    if (on) setView("table");
  }

  async function save(address: AddressRecord) {
    setSaving(true);
    setError("");
    try {
      await clientApi("/accounts/me/places", {
        method: "POST",
        body: JSON.stringify({ address_id: address.address_id }),
      });
      setSaved((current) => new Set(current).add(address.address_id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
    }
    setSaving(false);
  }

  function download() {
    const blob = new Blob([toCsv(shown)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `addresses-${shown.length}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const tones: PointTone[] = ["resolved", "pending"];

  return (
    <div className="space-y-5">
      <SectionTitle
        title={heading}
        hint={
          hint ??
          "Filter once; the map and the table show the same rows. A pin is the geocoder's own coordinate, so an address it could not place has none rather than an approximate one."
        }
        right={<ViewToggle view={view} onChange={setView} label="Address view" />}
      />

      {/* ------------------------------------------------------- the filters */}
      {/* Three controls at rest. Nine inputs laid out at once read as a form
          to be filled in rather than a way to narrow a list, and eight of the
          nine are things somebody wants occasionally. Search, state and
          jurisdiction stay out; the rest is one click away and says how much
          of it is in use. */}
      <div className="filters">
        <div className="filter-row">
          <label className="filter-field" style={{ flex: "1 1 260px" }}>
            <span>Search</span>
            <input
              className="field"
              value={filters.q}
              placeholder="Street, city, ZIP or id"
              onChange={(event) => set("q", event.target.value)}
            />
          </label>

          <label className="filter-field">
            <span>State</span>
            <select
              className="field"
              value={filters.state}
              onChange={(event) => set("state", event.target.value)}
            >
              <option value="">Every state</option>
              {Object.entries(stats?.by_state ?? {})
                .sort((a, b) => b[1] - a[1])
                .map(([code, count]) => (
                  <option key={code} value={code}>
                    {code} ({count})
                  </option>
                ))}
            </select>
          </label>

          <label className="filter-field">
            <span>Jurisdiction</span>
            <select
              className="field"
              value={filters.status}
              onChange={(event) => set("status", event.target.value as Filters["status"])}
            >
              <option value="all">Verified or not</option>
              <option value="resolved">Verified legal city</option>
              <option value="pending">Needs review</option>
            </select>
          </label>

          <div className="filter-field" style={{ minWidth: 0 }}>
            <span aria-hidden>&nbsp;</span>
            <button
              type="button"
              className="chip chip-more"
              aria-expanded={more}
              onClick={() => setMore(!more)}
            >
              {more ? "Fewer filters" : "More filters"}
              {advanced > 0 && <span className="chip-count">{advanced}</span>}
            </button>
          </div>
        </div>

        <div className="filter-group">
          <span>Legal city — click to highlight on the map</span>
          <div className="chips">
            {cityChips.map(({ city, count }) => (
              <button
                key={city}
                type="button"
                className="chip"
                aria-pressed={filters.cities.includes(city)}
                onClick={() => toggleCity(city)}
              >
                {city} <span className="chip-count">{count}</span>
              </button>
            ))}
            {facets.length > cityChips.length && (
              // A chip, not a dropdown. The native menu that used to sit in
              // this row drew its own stepper and its own grey, and read as a
              // different kind of thing from the eight options beside it.
              <button type="button" className="chip chip-more" onClick={() => setAllCities(true)}>
                +{facets.length - cityChips.length} more
              </button>
            )}
            {filters.cities.length > 0 && (
              <button
                type="button"
                className="chip"
                aria-pressed={filters.citiesNarrow}
                onClick={() => set("citiesNarrow", !filters.citiesNarrow)}
              >
                Only these
              </button>
            )}
          </div>
        </div>

        {more && (
          <div className="filter-more">
            <div className="filter-row">
              <label className="filter-field filter-narrow">
                <span>Built from</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={filters.builtFrom}
                  placeholder="1900"
                  onChange={(event) => set("builtFrom", event.target.value.replace(/\D/g, ""))}
                />
              </label>
              <label className="filter-field filter-narrow">
                <span>Built to</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={filters.builtTo}
                  placeholder="1979"
                  onChange={(event) => set("builtTo", event.target.value.replace(/\D/g, ""))}
                />
              </label>
              <label className="filter-field filter-narrow">
                <span>Units from</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={filters.unitsFrom}
                  placeholder="3"
                  onChange={(event) => set("unitsFrom", event.target.value.replace(/\D/g, ""))}
                />
              </label>
              <label className="filter-field filter-narrow">
                <span>Units to</span>
                <input
                  className="field"
                  inputMode="numeric"
                  value={filters.unitsTo}
                  placeholder="32"
                  onChange={(event) => set("unitsTo", event.target.value.replace(/\D/g, ""))}
                />
              </label>
            </div>

            <div className="filter-group">
              <span>Where the record is incomplete</span>
              <div className="chips">
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.zipOnly}
                  onClick={() => set("zipOnly", !filters.zipOnly)}
                >
                  ZIP disagrees{" "}
                  <span className="chip-count">
                    {countBy(addresses, (a) => a.zip_discrepancy)}
                  </span>
                </button>
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.missingYearBuilt}
                  onClick={() => set("missingYearBuilt", !filters.missingYearBuilt)}
                >
                  No year built{" "}
                  <span className="chip-count">{stats?.missing_year_built ?? "—"}</span>
                </button>
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.missingUnits}
                  onClick={() => set("missingUnits", !filters.missingUnits)}
                >
                  No unit count <span className="chip-count">{stats?.missing_units ?? "—"}</span>
                </button>
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.mappableOnly}
                  onClick={() => setCoordinate("mappableOnly", !filters.mappableOnly)}
                >
                  Has a coordinate{" "}
                  <span className="chip-count">{stats?.with_coordinates ?? "—"}</span>
                </button>
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.unplaceableOnly}
                  onClick={() => setCoordinate("unplaceableOnly", !filters.unplaceableOnly)}
                >
                  No coordinate{" "}
                  <span className="chip-count">
                    {stats ? stats.total - stats.with_coordinates : "—"}
                  </span>
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* --------------------------------------------------------- the count */}
      <div className="result-line">
        <span>
          <strong className="tabular-nums">{shown.length}</strong> of {addresses.length} addresses
        </span>
        {unplaceable > 0 && (
          <>
            <span style={{ color: "var(--warn)" }}>
              {unplaceable} cannot be placed on the map — no verified coordinate
            </span>
            <button type="button" className="btn btn-quiet" onClick={listUnplaceable}>
              {filters.unplaceableOnly ? "Show the placed ones too" : `List those ${unplaceable}`}
            </button>
          </>
        )}
        {filters.cities.length > 0 && (
          <span style={{ color: "var(--accent)" }}>
            highlighting {filters.cities.join(", ")}
          </span>
        )}
        <button type="button" className="btn btn-quiet" onClick={download}>
          Download these {shown.length} as CSV
        </button>
        {isActive(filters) && (
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              setFilters(EMPTY);
              setAllCities(false);
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}

      {/* ---------------------------------------------------------- the view */}
      {view === "map" ? (
        <AddressMap
          points={points}
          highlight={filters.cities}
          selectedId={selected}
          onSelect={setSelected}
          legend={tones}
          height={520}
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Address</Th>
              <Th>Postal city</Th>
              <Th>Legal city</Th>
              <Th>Jurisdiction</Th>
              <Th>ZIP check</Th>
              <Th>Built</Th>
              <Th>Units</Th>
              <Th>On the map</Th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, 500).map((address) => (
              <tr
                key={address.address_id}
                className={`clickable${selected === address.address_id ? " picked-row" : ""}`}
                onClick={() =>
                  setSelected(selected === address.address_id ? null : address.address_id)
                }
              >
                <Td className="mono">{address.address_id}</Td>
                <Td>{address.street_address}</Td>
                <Td>{address.postal_city}</Td>
                <Td>
                  {address.legal_city ?? "—"}
                  {address.postal_city_differs && (
                    <div className="text-xs" style={{ color: "var(--warn)" }}>
                      corrected
                    </div>
                  )}
                </Td>
                <Td>
                  <JurisdictionBadge status={address.jurisdiction_status} />
                </Td>
                <Td>{address.zip_discrepancy ? <ZipDiscrepancyBadge /> : "—"}</Td>
                <Td className="tabular-nums">
                  {address.year_built ?? <span style={{ color: "var(--faint)" }}>—</span>}
                </Td>
                <Td className="tabular-nums">
                  {address.units ?? <span style={{ color: "var(--faint)" }}>—</span>}
                </Td>
                <Td className="text-xs">
                  {placeable(address) ? (
                    "yes"
                  ) : (
                    <span style={{ color: "var(--warn)" }}>no coordinate</span>
                  )}
                </Td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={9} className="px-5 py-8 text-center text-sm" style={{ color: "var(--muted)" }}>
                  Nothing matches those filters.
                </td>
              </tr>
            )}
          </tbody>
        </Table>
      )}

      {/* ------------------------------------------------------ the selection */}
      {picked && (
        <div className="picked">
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
            <div>
              <div className="text-lg font-medium">{picked.street_address}</div>
              <div className="text-sm" style={{ color: "var(--muted)" }}>
                {picked.jurisdiction_status === "resolved"
                  ? `${picked.legal_city}, ${picked.legal_state ?? picked.state}`
                  : `${picked.postal_city}, ${picked.state} — mailing city, legal city not verified`}
                {picked.county ? ` · ${picked.county}` : ""}
              </div>
              <div className="mt-1 text-sm" style={{ color: "var(--faint)" }}>
                {picked.year_built ? `Built ${picked.year_built}` : "Year built not in the record"}
                {" · "}
                {picked.units ? `${picked.units} units` : "Unit count not in the record"}
                {picked.use_description ? ` · ${picked.use_description}` : ""}
              </div>
              {picked.postal_city_differs && (
                <p className="mt-2 max-w-xl text-sm">
                  The post says <strong>{picked.postal_city}</strong>, but the rules
                  that reach this building are {picked.legal_city}&rsquo;s.
                </p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {picked.legal_city && (
                // The corpus spells a city jurisdiction "Berkeley, CA", so the
                // link carries that spelling rather than the bare city name —
                // otherwise it lands on a rules page filtered to nothing.
                <Link
                  className="btn btn-quiet"
                  href={`/rules?jurisdiction=${encodeURIComponent(
                    `${picked.legal_city}, ${picked.legal_state ?? picked.state}`,
                  )}`}
                >
                  Rules for {picked.legal_city}
                </Link>
              )}
              {canSave && (
                <button
                  type="button"
                  className="btn"
                  disabled={saving || saved.has(picked.address_id)}
                  onClick={() => save(picked)}
                >
                  {saved.has(picked.address_id) ? "Saved" : "Save to your addresses"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function countBy<T>(rows: T[], predicate: (row: T) => boolean): number {
  return rows.reduce((total, row) => total + (predicate(row) ? 1 : 0), 0);
}
