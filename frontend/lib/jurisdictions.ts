/** Putting a rule on a map.
 *
 * A rule has a jurisdiction, not a coordinate: `"CA"` or `"Berkeley, CA"` is
 * the only geography in a rule record. The only coordinates in this system
 * belong to addresses, so a jurisdiction is drawn where its addresses are —
 * the mean of the points the geocoder returned for the buildings we hold in
 * it.
 *
 * That has to be said out loud wherever it is drawn, because a reader will
 * otherwise take the bubble for the city. It is the centre of our rows in
 * that jurisdiction and nothing more: a city where we hold three addresses is
 * marked at the middle of those three, not at its town hall, and a
 * jurisdiction we hold no address in cannot be drawn at all. Santa Ana has
 * rules in this corpus and no address on file, and the rules view counts those
 * off to one side rather than dropping them.
 *
 * Each jurisdiction carries every name it goes by, because the two sides spell
 * them differently and neither spelling is wrong: the corpus says
 * `"Berkeley, CA"` and `"CA"`, an address says `legal_city: "Berkeley"` and
 * `state: "CA"`, and a link written out in full says `"California"`. Matching
 * on one spelling and hoping is how a jurisdiction silently vanishes.
 */

import type { AddressRecord } from "./types";

/** The states on file, by the name somebody might write out in full.
 *
 * The corpus uses the two-letter code, which needs no table — this is only
 * here so a filter or a link spelled out still resolves.
 */
export const STATE_NAMES: Record<string, string> = {
  CA: "California",
  MA: "Massachusetts",
  NJ: "New Jersey",
};

export interface Anchor {
  /** The jurisdiction as a rule record names it, e.g. `Berkeley, CA`. */
  name: string;
  /** Every other spelling that should resolve to this same point. */
  aliases: string[];
  lon: number;
  lat: number;
  /** How many addresses on file the point was averaged over. */
  addresses: number;
  level: "state" | "city";
}

function mean(rows: { latitude: number | null; longitude: number | null }[]) {
  const placed = rows.filter(
    (row): row is { latitude: number; longitude: number } =>
      typeof row.latitude === "number" && typeof row.longitude === "number",
  );
  if (placed.length === 0) return null;
  return {
    lon: placed.reduce((sum, row) => sum + row.longitude, 0) / placed.length,
    lat: placed.reduce((sum, row) => sum + row.latitude, 0) / placed.length,
    addresses: placed.length,
  };
}

/** One anchor per jurisdiction on file, at both levels.
 *
 * A list rather than a map, because this crosses to a client component and a
 * `Map` does not survive serialisation. `indexBy` turns it back into one.
 *
 * A mailing city is never an anchor: it is not what rules attach to, and a
 * bubble drawn at one would attribute a city's rules to buildings that are not
 * in it.
 */
export function anchorList(addresses: AddressRecord[]): Anchor[] {
  const byState = new Map<string, AddressRecord[]>();
  const byCity = new Map<string, AddressRecord[]>();
  for (const address of addresses) {
    byState.set(address.state, [...(byState.get(address.state) ?? []), address]);
    if (address.jurisdiction_status === "resolved" && address.legal_city) {
      const key = `${address.legal_city}|${address.legal_state ?? address.state}`;
      byCity.set(key, [...(byCity.get(key) ?? []), address]);
    }
  }

  const out: Anchor[] = [];

  for (const [code, rows] of byState) {
    const centre = mean(rows);
    if (!centre) continue;
    // Named in full where we know the full name, because the label on the map
    // should read "California" rather than "CA" beside "Berkeley, CA". The
    // code stays as an alias — it is what the corpus actually writes.
    out.push({
      name: STATE_NAMES[code] ?? code,
      aliases: STATE_NAMES[code] ? [code] : [],
      level: "state",
      ...centre,
    });
  }

  for (const [key, rows] of byCity) {
    const centre = mean(rows);
    if (!centre) continue;
    const [city, state] = key.split("|");
    // `City, ST` is the corpus spelling, so it is the anchor's own name — a
    // bubble should be labelled the way the rules behind it are.
    out.push({ name: `${city}, ${state}`, aliases: [city], level: "city", ...centre });
  }

  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/** The lookup, keyed under every spelling, lowercased.
 *
 * A city key never overwrites a state one or vice versa; first registration
 * wins, and the two never collide in this data.
 */
export function indexBy(list: Anchor[]): Map<string, Anchor> {
  const out = new Map<string, Anchor>();
  for (const anchor of list) {
    for (const name of [anchor.name, ...anchor.aliases]) {
      const key = name.trim().toLowerCase();
      if (key && !out.has(key)) out.set(key, anchor);
    }
  }
  return out;
}

/** The anchor for one rule's jurisdiction, or null if we hold no address in it.
 *
 * Null is a real answer and the view says so: the corpus covers jurisdictions
 * the address book does not reach, and silently dropping those rules off a
 * map would make coverage look better than it is.
 */
export function anchorFor(jurisdiction: string, index: Map<string, Anchor>): Anchor | null {
  return index.get(jurisdiction.trim().toLowerCase()) ?? null;
}
