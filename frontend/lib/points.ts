/** Saved buildings as map points.
 *
 * Shared by the four dashboards so that "yours, and nothing is blocked" is the
 * same green everywhere. The tone is the only thing that differs between the
 * roles, which is why it is a parameter and not a branch.
 */

import type { MapPoint, PointTone } from "@/components/map/model";
import type { Place } from "./types";

export function placePoint(
  place: Place,
  tone: PointTone,
  extra: string[] = [],
): MapPoint | null {
  if (place.latitude === null || place.longitude === null) return null;
  return {
    id: place.address_id,
    lon: place.longitude,
    lat: place.latitude,
    title: place.label ?? place.street_address,
    subtitle:
      place.jurisdiction_status === "resolved"
        ? `${place.legal_city}, ${place.legal_state ?? place.state}`
        : `${place.postal_city}, ${place.state} — mailing city, legal city not verified`,
    jurisdiction: place.legal_city,
    state: place.state,
    tone,
    detail: [
      place.year_built ? `Built ${place.year_built}` : "Year built not in the record",
      place.units ? `${place.units} units` : "Unit count not in the record",
      ...extra,
    ],
  };
}

/** How many saved buildings have no coordinate to draw.
 *
 * Reported rather than hidden: an unresolved address, and one a human resolved
 * by override, both come back without a point. A map that silently drew four
 * of five pins would make the fifth building disappear.
 */
export function unplaced(places: Place[]): number {
  return places.filter((place) => place.latitude === null || place.longitude === null).length;
}
