/** What the map draws, and in what colour.
 *
 * Deliberately not "an address": the same component plots a renter's one home,
 * five hundred rows on file coloured by whether their jurisdiction is verified,
 * and the buildings one change case moves. A point therefore carries its own
 * tone and its own caption rather than being re-derived from a record type the
 * map would have to know about.
 */

export interface MapPoint {
  id: string;
  lon: number;
  lat: number;
  title: string;
  /** One line under the title in the popup. */
  subtitle?: string;
  /** The legal city, which is what a jurisdiction highlight matches on. */
  jurisdiction: string | null;
  state: string;
  tone: PointTone;
  /** Extra lines in the popup: built, units, badges. */
  detail?: string[];
  /** How much this point stands for, when one circle is many things.
   *
   * The jurisdiction view draws one bubble per city rather than one per
   * building, and the bubble has to say how many rules are behind it. Area is
   * proportional to the weight — never radius, which would triple the apparent
   * size of a city with three times the rules. */
  weight?: number;
  /** Which side of the point its label sits on.
   *
   * Our Massachusetts rows are Boston and Cambridge, so the state's centroid
   * lands almost exactly on Boston's — two true points that happen to
   * coincide. Moving one of them would be a lie; printing their labels on
   * opposite sides is not. */
  labelAnchor?: "top" | "bottom";
}

export type PointTone =
  | "applies"
  | "blocked"
  | "resolved"
  | "pending"
  | "mine"
  | "affected"
  | "conflict"
  | "neutral";

/** The palette, duplicated from globals.css on purpose.
 *
 * WebGL paint properties take colours, not custom properties: `var(--good)`
 * means nothing to a style layer. These are the same six values the badges
 * use, so a green pin and a green badge are the same green — and the legend
 * below every map states each colour in words, because colour alone never
 * carries meaning here.
 */
export const TONES: Record<PointTone, { color: string; label: string }> = {
  applies: { color: "#2f7d57", label: "Rules apply, nothing blocked" },
  blocked: { color: "#b5812a", label: "An answer is blocked by a missing fact" },
  resolved: { color: "#2f7d57", label: "Legal jurisdiction verified" },
  pending: { color: "#b5812a", label: "Jurisdiction needs review" },
  mine: { color: "#35635c", label: "Yours" },
  affected: { color: "#a96a4a", label: "Moved by this change case" },
  // Deliberately far from `affected`: the two appear on the same map, and two
  // browns a shade apart is a legend nobody can use.
  conflict: { color: "#7a2a33", label: "Flagged for review" },
  neutral: { color: "#7d9197", label: "On file" },
};

/** The convex hull of a set of points, as a closed ring.
 *
 * Used to shade the extent of one jurisdiction. It is emphatically **not** a
 * city boundary and the map says so where it is drawn: we hold no boundary
 * geometry, only the coordinates the geocoder returned for the addresses we
 * have. Drawing a hull and letting somebody read it as the city limits would
 * be the most consequential lie this app could tell, so the label is part of
 * the feature rather than a caption somebody might miss.
 *
 * Andrew's monotone chain: sort, then one pass up and one pass down.
 */
export function hull(points: { lon: number; lat: number }[]): [number, number][] {
  if (points.length < 3) return [];
  const sorted = [...points]
    .map((p) => [p.lon, p.lat] as [number, number])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);

  const cross = (o: number[], a: number[], b: number[]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

  const build = (input: [number, number][]) => {
    const stack: [number, number][] = [];
    for (const point of input) {
      while (stack.length >= 2 && cross(stack[stack.length - 2], stack[stack.length - 1], point) <= 0) {
        stack.pop();
      }
      stack.push(point);
    }
    return stack;
  };

  const lower = build(sorted);
  const upper = build([...sorted].reverse());
  const ring = [...lower.slice(0, -1), ...upper.slice(0, -1)];
  return ring.length >= 3 ? [...ring, ring[0]] : [];
}

/** The bounding box of a set of points, padded so pins are not against the edge. */
export function bounds(points: MapPoint[]): [[number, number], [number, number]] | null {
  if (points.length === 0) return null;
  let west = points[0].lon;
  let east = points[0].lon;
  let south = points[0].lat;
  let north = points[0].lat;
  for (const point of points) {
    west = Math.min(west, point.lon);
    east = Math.max(east, point.lon);
    south = Math.min(south, point.lat);
    north = Math.max(north, point.lat);
  }
  // A single point has no extent, so give it one — otherwise fitBounds zooms
  // to the maximum and a renter's home fills the screen with one roof.
  const padLon = Math.max((east - west) * 0.12, 0.02);
  const padLat = Math.max((north - south) * 0.12, 0.02);
  return [
    [west - padLon, south - padLat],
    [east + padLon, north + padLat],
  ];
}
