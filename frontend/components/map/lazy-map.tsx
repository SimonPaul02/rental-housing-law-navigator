"use client";

import dynamic from "next/dynamic";

/** The map, loaded only when somebody looks at one.
 *
 * A WebGL map library is a few hundred kilobytes, and three of the four pages
 * that offer a map open on the table — the rules view in particular, where
 * text is the point and the map answers one question. Importing it statically
 * would make every one of those pages pay for a canvas nobody has asked for
 * yet, so the import happens on the switch instead.
 *
 * `ssr: false` because it needs a canvas: there is nothing for the server to
 * render, and pretending otherwise only produces a hydration mismatch.
 */
export const LazyAddressMap = dynamic(
  () => import("./address-map").then((module) => module.AddressMap),
  {
    ssr: false,
    loading: () => (
      <div className="map-shell">
        <div className="map-canvas" style={{ height: 420 }} aria-hidden />
      </div>
    ),
  },
);
