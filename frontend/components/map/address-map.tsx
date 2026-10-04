"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  FullscreenControl,
  GeoJSONSource,
  Map as MapLibre,
  Marker,
  NavigationControl,
  Popup,
  ScaleControl,
  type ErrorEvent,
  type LngLatBoundsLike,
  type MapLayerMouseEvent,
  setWorkerUrl,
  type DataDrivenPropertyValueSpecification,
  type MapMouseEvent,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { TONES, bounds, hull, type MapPoint, type PointTone } from "./model";

/** The map every role sees, with a different set of points in it.
 *
 * WebGL rather than DOM markers, because the agency view puts five hundred
 * buildings on one canvas and five hundred absolutely positioned divs would
 * stutter on every pan. One circle layer draws them all, and the radius grows
 * with zoom so the same layer reads as density when you are looking at a state
 * and as individual buildings when you are looking at a street.
 *
 * There is no clustering, which is a choice rather than an omission: a cluster
 * badge needs a glyph server, and a map that silently loses its labels when a
 * font fails to fetch is worse than one that never promised them. The counts
 * live in the filter panel, which is always right.
 *
 * **A map is never the only way to read this data.** Every page that offers one
 * also offers the table, and the table is the accessible and printable
 * version — see `ViewToggle`. Colour never carries meaning alone: the legend
 * states every tone in words.
 */

/** A light, label-rich vector basemap that needs no API key.
 *
 * Overridable per deployment, because a basemap is somebody else's service and
 * this app should not fall over when it changes its terms.
 */
const STYLE_URL =
  process.env.NEXT_PUBLIC_MAP_STYLE ??
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

/** What the map falls back to when the basemap will not load.
 *
 * Not an error state: the pins, the highlight and the popups all still work,
 * and the geography they sit in is the part that was borrowed. A demo on a
 * blocked network keeps its map rather than losing the page.
 */
const BARE_STYLE = {
  version: 8 as const,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background" as const,
      paint: { "background-color": "#e4edeb" },
    },
  ],
};

const SOURCE = "points";
const HULL_SOURCE = "extent";

/** Tell MapLibre where its own worker is.
 *
 * It normally works this out from `import.meta.url`, which a bundler rewrites
 * — so under webpack it asks for an empty URL, the worker never starts, and
 * the map renders as a blank rectangle with no error a reader could act on.
 * The file is staged into `public/maplibre/` at build time by
 * `scripts/copy-map-worker.mjs`; this points at it.
 *
 * Module scope, so it happens once per page rather than once per map.
 */
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

export interface AddressMapProps {
  points: MapPoint[];
  /** Legal cities to pick out. Their pins get a ring, and their extent is shaded. */
  highlight?: string[];
  /** Shade the extent but leave the pins alone.
   *
   * For a map where everything drawn is already the highlighted set — a change
   * case's own affected buildings — a ring on every pin says nothing and only
   * makes a dense map denser. */
  shadeOnly?: boolean;
  /** The point to open a popup for, driven from outside (a table row click). */
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  height?: number;
  /** Tones to describe in the legend. Defaults to the ones actually present. */
  legend?: PointTone[];
  /** `weight` sizes each circle by what it stands for, for the one-bubble-per
   *  jurisdiction view. `zoom` is the default and sizes every pin alike. */
  sizeBy?: "zoom" | "weight";
  /** Draw each point's title beside it.
   *
   * DOM labels rather than a symbol layer, deliberately: a text layer needs a
   * glyph server, and a map that silently loses its labels when a font request
   * fails is worse than one that never had them. Only for the handful-of-points
   * case — a label per building would be unreadable anyway. */
  labels?: boolean;
}

export function AddressMap({
  points,
  highlight = [],
  selectedId = null,
  onSelect,
  height = 460,
  legend,
  sizeBy = "zoom",
  labels = false,
  shadeOnly = false,
}: AddressMapProps) {
  const holder = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibre | null>(null);
  const popup = useRef<Popup | null>(null);
  const markers = useRef<Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState<"loading" | "live" | "bare">("loading");

  const byId = useMemo(() => new Map(points.map((p) => [p.id, p])), [points]);

  const collection = useMemo(
    () => ({
      type: "FeatureCollection" as const,
      features: points.map((point) => ({
        type: "Feature" as const,
        id: point.id,
        geometry: { type: "Point" as const, coordinates: [point.lon, point.lat] },
        properties: {
          id: point.id,
          title: point.title,
          tone: point.tone,
          color: TONES[point.tone].color,
          jurisdiction: point.jurisdiction ?? "",
          weight: point.weight ?? 1,
        },
      })),
    }),
    [points],
  );

  /** The shaded extent of each highlighted jurisdiction.
   *
   * One polygon per city rather than one for all of them: Los Angeles and
   * Boston highlighted together must not be joined by a band across the
   * continent, which is exactly what a single hull of both would draw.
   */
  const extent = useMemo(() => {
    const wanted = new Set(highlight.map((name) => name.toLowerCase()));
    if (wanted.size === 0) {
      return { type: "FeatureCollection" as const, features: [] };
    }
    const groups = new Map<string, MapPoint[]>();
    for (const point of points) {
      const key = point.jurisdiction?.toLowerCase();
      if (key && wanted.has(key)) {
        groups.set(key, [...(groups.get(key) ?? []), point]);
      }
    }
    return {
      type: "FeatureCollection" as const,
      features: [...groups.entries()].flatMap(([key, group]) => {
        const ring = hull(group);
        if (ring.length < 4) return [];
        return [
          {
            type: "Feature" as const,
            geometry: { type: "Polygon" as const, coordinates: [ring] },
            properties: { jurisdiction: key },
          },
        ];
      }),
    };
  }, [points, highlight]);

  // ------------------------------------------------------------- the map
  useEffect(() => {
    if (!holder.current || map.current) return;

    const instance = new MapLibre({
      container: holder.current,
      style: STYLE_URL,
      center: [-110, 37],
      zoom: 3,
      attributionControl: { compact: true },
      // Pitch and rotation are off: this is a data map, and a tilted one makes
      // two pins at different latitudes look like different distances.
      pitchWithRotate: false,
      dragRotate: false,
      touchZoomRotate: true,
    });
    instance.touchZoomRotate.disableRotation();
    instance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    instance.addControl(new FullscreenControl(), "top-right");
    instance.addControl(new ScaleControl({ unit: "imperial" }), "bottom-left");

    // Area proportional to weight, hence the square root: a city with four
    // times the rules gets a bubble twice as wide, which is the only scaling a
    // reader estimates correctly.
    const radius: DataDrivenPropertyValueSpecification<number> =
      sizeBy === "weight"
        ? [
            "*",
            ["interpolate", ["linear"], ["zoom"], 3, 0.85, 7, 1, 12, 1.5],
            ["interpolate", ["linear"], ["sqrt", ["get", "weight"]], 0, 6, 12, 34],
          ]
        : ["interpolate", ["linear"], ["zoom"], 3, 4, 7, 6, 11, 8.5, 15, 13];

    const paint = () => {
      if (instance.getSource(SOURCE)) return;

      instance.addSource(HULL_SOURCE, { type: "geojson", data: extent });
      instance.addLayer({
        id: "extent-fill",
        type: "fill",
        source: HULL_SOURCE,
        paint: { "fill-color": "#35635c", "fill-opacity": 0.1 },
      });
      instance.addLayer({
        id: "extent-line",
        type: "line",
        source: HULL_SOURCE,
        paint: {
          "line-color": "#35635c",
          "line-width": 1.5,
          "line-opacity": 0.6,
          "line-dasharray": [2, 2],
        },
      });

      instance.addSource(SOURCE, { type: "geojson", data: collection });

      // A soft halo under every pin, so a dense cluster reads as density
      // rather than as a single blob of colour.
      instance.addLayer({
        id: "point-halo",
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-color": ["get", "color"],
          "circle-opacity": 0.2,
          "circle-radius": ["*", radius, 2.1],
        },
      });

      instance.addLayer({
        id: "point",
        type: "circle",
        source: SOURCE,
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": radius,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            3, 0.5,
            11, 1.4,
          ],
          "circle-stroke-opacity": 0.9,
        },
      });

      // The ring that picks out a highlighted jurisdiction, and the selected
      // pin. Drawn as its own layer with a filter rather than by recolouring,
      // so the tone still says what the building's answer is.
      instance.addLayer({
        id: "point-ring",
        type: "circle",
        source: SOURCE,
        filter: ["==", ["get", "id"], "__none__"],
        paint: {
          "circle-color": "rgba(0,0,0,0)",
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            3, 6,
            11, 13,
            15, 19,
          ],
          "circle-stroke-color": "#1e3a36",
          "circle-stroke-width": 2,
        },
      });

      instance.addLayer({
        id: "point-selected",
        type: "circle",
        source: SOURCE,
        filter: ["==", ["get", "id"], "__none__"],
        paint: {
          "circle-color": "#1e3a36",
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            3, 5,
            11, 9,
            15, 14,
          ],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2.5,
        },
      });

      instance.on("mouseenter", "point", () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", "point", () => {
        instance.getCanvas().style.cursor = "";
      });
      instance.on("click", "point", (event: MapLayerMouseEvent) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === "string") onSelect?.(id);
      });
      instance.on("click", (event: MapMouseEvent) => {
        // A click on the basemap clears the selection, which is what somebody
        // expects when they click away from a card.
        const hits = instance.queryRenderedFeatures(event.point, { layers: ["point"] });
        if (hits.length === 0) onSelect?.(null);
      });

      setReady(true);
    };

    instance.on("load", () => {
      setBasemap("live");
      paint();
    });

    // A basemap is somebody else's server. If it will not answer, swap in the
    // bare style and carry on — the data is ours and still draws.
    instance.on("error", (event: ErrorEvent) => {
      const message = String(event.error ?? "");
      if (!instance.isStyleLoaded() && /style|sprite|glyph|tiles/i.test(message)) {
        setBasemap((current) => {
          if (current === "bare") return current;
          instance.setStyle(BARE_STYLE);
          return "bare";
        });
      }
    });
    instance.on("styledata", () => {
      if (instance.isStyleLoaded()) paint();
    });

    map.current = instance;
    return () => {
      popup.current?.remove();
      instance.remove();
      map.current = null;
      setReady(false);
    };
    // Built once. Every later change flows through the effects below, because
    // tearing down a WebGL context to move a pin would be absurd.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------- the data
  useEffect(() => {
    if (!ready || !map.current) return;
    const source = map.current.getSource(SOURCE);
    if (source instanceof GeoJSONSource) source.setData(collection);
  }, [ready, collection]);

  useEffect(() => {
    if (!ready || !map.current) return;
    const instance = map.current;
    for (const marker of markers.current) marker.remove();
    markers.current = [];
    if (!labels) return;
    // Pushed clear of its own circle, by that circle's own size. Without this
    // a label sits exactly on the bubble it names and hides the thing it is
    // labelling — worst for the biggest bubbles, which are the ones most worth
    // seeing.
    const clearance = (point: MapPoint) =>
      sizeBy === "weight"
        ? 10 + 28 * Math.min(Math.sqrt(point.weight ?? 1) / 12, 1)
        : 14;
    for (const point of points) {
      const above = point.labelAnchor === "bottom";
      const element = document.createElement("button");
      element.type = "button";
      element.className = "map-label";
      element.textContent = point.title;
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect?.(point.id);
      });
      markers.current.push(
        new Marker({
          element,
          anchor: above ? "bottom" : "top",
          offset: [0, above ? -clearance(point) : clearance(point)],
        })
          .setLngLat([point.lon, point.lat])
          .addTo(instance),
      );
    }

    // A street address labelled from three thousand miles up is a smear of
    // overlapping pills that hides the pins underneath it. Bubbles are
    // different: a jurisdiction label is the whole point of that view and
    // stays at every zoom.
    const retitle = () => {
      const show = sizeBy === "weight" || instance.getZoom() >= 6;
      for (const marker of markers.current) {
        marker.getElement().style.display = show ? "" : "none";
      }
    };
    retitle();
    instance.on("zoomend", retitle);
    return () => {
      instance.off("zoomend", retitle);
      for (const marker of markers.current) marker.remove();
      markers.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, labels, points, sizeBy]);

  useEffect(() => {
    if (!ready || !map.current) return;
    const source = map.current.getSource(HULL_SOURCE);
    if (source instanceof GeoJSONSource) source.setData(extent);
  }, [ready, extent]);

  // Fit to whatever is currently shown. Filtering down to one city should move
  // the camera there — a filter that leaves you looking at the wrong state is
  // a filter nobody trusts.
  useEffect(() => {
    if (!ready || !map.current) return;
    const box = bounds(points);
    if (!box) return;
    map.current.fitBounds(box as LngLatBoundsLike, {
      padding: 48,
      // A jurisdiction bubble is a derived point — the mean of a city's rows
      // addresses — so zooming it to street level would invite somebody to
      // read a particular corner as meaningful. Real addresses are exact and
      // can be zoomed all the way in.
      maxZoom: sizeBy === "weight" ? 8 : points.length === 1 ? 15 : 13,
      duration: 650,
    });
  }, [ready, points, sizeBy]);

  useEffect(() => {
    if (!ready || !map.current) return;
    const lowered = shadeOnly ? [] : highlight.map((name) => name.toLowerCase());
    map.current.setFilter(
      "point-ring",
      lowered.length
        ? ["in", ["downcase", ["get", "jurisdiction"]], ["literal", lowered]]
        : ["==", ["get", "id"], "__none__"],
    );
  }, [ready, highlight, shadeOnly]);

  // ------------------------------------------------------- the open popup
  useEffect(() => {
    if (!ready || !map.current) return;
    const instance = map.current;
    instance.setFilter(
      "point-selected",
      selectedId ? ["==", ["get", "id"], selectedId] : ["==", ["get", "id"], "__none__"],
    );

    popup.current?.remove();
    popup.current = null;
    if (!selectedId) return;

    const point = byId.get(selectedId);
    if (!point) return;

    popup.current = new Popup({
      closeButton: true,
      closeOnClick: false,
      offset: 14,
      maxWidth: "280px",
      className: "map-popup",
    })
      .setLngLat([point.lon, point.lat])
      .setDOMContent(card(point))
      .addTo(instance);

    popup.current.on("close", () => onSelect?.(null));
    // `onSelect` is stable enough in practice and including it would reopen the
    // popup on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, selectedId, byId]);

  const shown = legend ?? ([...new Set(points.map((p) => p.tone))] as PointTone[]);
  const placed = points.length;
  // Only the jurisdictions a hull was actually drawn for. A city holding one
  // or two of our addresses has no area to shade, and listing it in the legend
  // would promise a shape that is not on the map.
  const shaded = highlight.filter((name) =>
    extent.features.some((feature) => feature.properties.jurisdiction === name.toLowerCase()),
  );

  return (
    <div className="map-shell">
      <div
        ref={holder}
        className="map-canvas"
        style={{ height }}
        role="application"
        aria-label={`Map of ${placed} ${placed === 1 ? "address" : "addresses"}. The table view below carries the same rows.`}
      />
      <div className="map-legend">
        {shown.map((tone) => (
          <span key={tone} className="map-key">
            <span className="map-dot" style={{ background: TONES[tone].color }} aria-hidden />
            {TONES[tone].label}
          </span>
        ))}
        {extent.features.length > 0 && (
          <span className="map-key">
            <span className="map-dot map-dot-hull" aria-hidden />
            Shaded: the extent of the addresses we hold in{" "}
            {shaded.join(", ")} — not a legal boundary
          </span>
        )}
        {basemap === "bare" && (
          <span className="map-key" style={{ color: "var(--warn)" }}>
            The basemap could not be loaded; the pins are still exact.
          </span>
        )}
      </div>
    </div>
  );
}

/** The popup, built as DOM rather than an HTML string.
 *
 * A street address is user-adjacent data and this is the one place it is
 * injected into a third-party widget, so it goes in as text nodes. There is
 * nothing to escape because nothing is parsed.
 */
function card(point: MapPoint): HTMLElement {
  const root = document.createElement("div");
  root.className = "map-card";

  const title = document.createElement("div");
  title.className = "map-card-title";
  title.textContent = point.title;
  root.append(title);

  if (point.subtitle) {
    const sub = document.createElement("div");
    sub.className = "map-card-sub";
    sub.textContent = point.subtitle;
    root.append(sub);
  }

  for (const line of point.detail ?? []) {
    const row = document.createElement("div");
    row.className = "map-card-line";
    row.textContent = line;
    root.append(row);
  }

  const tone = document.createElement("div");
  tone.className = "map-card-tone";
  const dot = document.createElement("span");
  dot.className = "map-dot";
  dot.style.background = TONES[point.tone].color;
  tone.append(dot, document.createTextNode(TONES[point.tone].label));
  root.append(tone);

  return root;
}
