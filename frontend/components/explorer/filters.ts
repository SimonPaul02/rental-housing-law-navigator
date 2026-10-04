/** Narrowing five hundred addresses, in the browser.
 *
 * All of them are fetched once on the server and filtered here rather than
 * round-tripping per keystroke. That is a deliberate trade: the sample is five
 * hundred rows and will not grow, the API already caps a page at exactly that,
 * and filtering locally is what lets the map and the table stay in step with a
 * slider as it moves. A dataset that could grow would have to do this
 * server-side, and the predicate below is written as one function precisely so
 * that move would be a port rather than a rewrite.
 */

import type { MapPoint, PointTone } from "@/components/map/model";
import type { AddressRecord } from "@/lib/types";

export type StatusFilter = "all" | "resolved" | "pending";

export interface Filters {
  q: string;
  state: string;
  /** Verified legal cities. Highlighted on the map; narrowing is opt-in. */
  cities: string[];
  /** When true the chosen jurisdictions also hide everything else. */
  citiesNarrow: boolean;
  status: StatusFilter;
  zipOnly: boolean;
  missingYearBuilt: boolean;
  missingUnits: boolean;
  mappableOnly: boolean;
  /** The other half of `mappableOnly`: only the rows with no coordinate. */
  unplaceableOnly: boolean;
  builtFrom: string;
  builtTo: string;
  unitsFrom: string;
  unitsTo: string;
}

export const EMPTY: Filters = {
  q: "",
  state: "",
  cities: [],
  citiesNarrow: false,
  status: "all",
  zipOnly: false,
  missingYearBuilt: false,
  missingUnits: false,
  mappableOnly: false,
  unplaceableOnly: false,
  builtFrom: "",
  builtTo: "",
  unitsFrom: "",
  unitsTo: "",
};

export function isActive(filters: Filters): boolean {
  return (
    filters.q.trim() !== "" ||
    filters.state !== "" ||
    (filters.cities.length > 0 && filters.citiesNarrow) ||
    filters.status !== "all" ||
    filters.zipOnly ||
    filters.missingYearBuilt ||
    filters.missingUnits ||
    filters.mappableOnly ||
    filters.unplaceableOnly ||
    filters.builtFrom !== "" ||
    filters.builtTo !== "" ||
    filters.unitsFrom !== "" ||
    filters.unitsTo !== ""
  );
}

function number(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

/** One row against one set of filters.
 *
 * A missing fact never passes a range test. "Built before 1979" is a claim
 * about a year we hold, and an address with no year built is not evidence for
 * either side of it — including it because the comparison happened to be
 * vacuous is how a coverage number becomes a lie. The `missing` toggles are
 * how somebody asks for those rows on purpose.
 */
export function matches(address: AddressRecord, filters: Filters): boolean {
  const needle = filters.q.trim().toLowerCase();
  if (needle) {
    const haystack = [
      address.street_address,
      address.postal_city,
      address.legal_city ?? "",
      address.address_id,
      address.zip ?? "",
    ]
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(needle)) return false;
  }

  if (filters.state && address.state !== filters.state) return false;

  if (filters.citiesNarrow && filters.cities.length > 0) {
    const city = address.legal_city?.toLowerCase();
    if (!city || !filters.cities.some((name) => name.toLowerCase() === city)) return false;
  }

  if (filters.status === "resolved" && address.jurisdiction_status !== "resolved") return false;
  if (filters.status === "pending" && address.jurisdiction_status === "resolved") return false;

  if (filters.zipOnly && !address.zip_discrepancy) return false;
  if (filters.missingYearBuilt && address.year_built !== null) return false;
  if (filters.missingUnits && address.units !== null) return false;
  if (filters.mappableOnly && !placeable(address)) return false;
  if (filters.unplaceableOnly && placeable(address)) return false;

  const builtFrom = number(filters.builtFrom);
  const builtTo = number(filters.builtTo);
  if (builtFrom !== null && (address.year_built === null || address.year_built < builtFrom)) {
    return false;
  }
  if (builtTo !== null && (address.year_built === null || address.year_built > builtTo)) {
    return false;
  }

  const unitsFrom = number(filters.unitsFrom);
  const unitsTo = number(filters.unitsTo);
  if (unitsFrom !== null && (address.units === null || address.units < unitsFrom)) return false;
  if (unitsTo !== null && (address.units === null || address.units > unitsTo)) return false;

  return true;
}

export function placeable(
  address: Pick<AddressRecord, "latitude" | "longitude">,
): boolean {
  return typeof address.latitude === "number" && typeof address.longitude === "number";
}

/** How many of the current rows each legal city holds.
 *
 * Counted over the rows that pass every filter *except* the jurisdiction one,
 * so a facet never shows a zero for a city you have selected — the classic
 * facet bug where picking one option makes the others look empty.
 */
export function cityCounts(
  addresses: AddressRecord[],
  filters: Filters,
): { city: string; count: number }[] {
  const without = { ...filters, cities: [], citiesNarrow: false };
  const tally = new Map<string, number>();
  for (const address of addresses) {
    if (!address.legal_city) continue;
    if (!matches(address, without)) continue;
    tally.set(address.legal_city, (tally.get(address.legal_city) ?? 0) + 1);
  }
  return [...tally.entries()]
    .map(([city, count]) => ({ city, count }))
    .sort((a, b) => b.count - a.count || a.city.localeCompare(b.city));
}

/** An address as a point, coloured by whether its jurisdiction is settled. */
export function toPoint(
  address: AddressRecord,
  tone?: PointTone,
): MapPoint | null {
  if (!placeable(address)) return null;
  const detail = [
    address.year_built ? `Built ${address.year_built}` : "Year built not in the record",
    address.units ? `${address.units} units` : "Unit count not in the record",
  ];
  if (address.postal_city_differs) {
    detail.push(`Post says ${address.postal_city} — rules are ${address.legal_city}'s`);
  }
  if (address.zip_discrepancy) detail.push("Supplied ZIP disagrees with the geocoder");

  return {
    id: address.address_id,
    lon: address.longitude as number,
    lat: address.latitude as number,
    title: address.street_address,
    subtitle:
      address.jurisdiction_status === "resolved"
        ? `${address.legal_city}, ${address.legal_state ?? address.state}`
        : `${address.postal_city}, ${address.state} — mailing city`,
    jurisdiction: address.legal_city,
    state: address.state,
    tone: tone ?? (address.jurisdiction_status === "resolved" ? "resolved" : "pending"),
    detail,
  };
}

/** The filtered rows as a CSV, for somebody who has to work in a spreadsheet.
 *
 * An agency reviewing a thousand records does not do it in a browser, and
 * refusing to hand over the selection they just built would only mean they
 * rebuild it by hand somewhere else.
 */
export function toCsv(addresses: AddressRecord[]): string {
  const columns: (keyof AddressRecord)[] = [
    "address_id",
    "street_address",
    "postal_city",
    "state",
    "zip",
    "legal_city",
    "legal_state",
    "county",
    "jurisdiction_status",
    "zip_discrepancy",
    "year_built",
    "units",
    "latitude",
    "longitude",
  ];
  const cell = (value: unknown) => {
    if (value === null || value === undefined) return "";
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [
    columns.join(","),
    ...addresses.map((address) => columns.map((column) => cell(address[column])).join(",")),
  ].join("\n");
}
