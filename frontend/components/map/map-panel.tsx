"use client";

import { useState, type ReactNode } from "react";
import { ViewToggle, type View } from "@/components/view-toggle";
import { SectionTitle } from "@/components/ui";
import { LazyAddressMap as AddressMap } from "./lazy-map";
import type { MapPoint, PointTone } from "./model";

/** A map a server component can drop in, with the table beside it.
 *
 * Two jobs, both of which have to happen in the browser: holding which pin is
 * open, and holding which of the two views is showing. The table arrives as a
 * prop — server-rendered markup handed across the boundary — so a dashboard
 * keeps rendering its own table on the server and still gets a toggle, without
 * the table's data or its components crossing into the client bundle.
 *
 * With no `table`, there is no toggle: a renter's single home has nothing to
 * tabulate, and a control with one option is just noise.
 */
export function MapPanel({
  points,
  table,
  title,
  hint,
  highlight,
  shadeOnly,
  legend,
  height = 420,
  labels,
  initialView = "map",
  footnote,
}: {
  points: MapPoint[];
  table?: ReactNode;
  title?: string;
  hint?: string;
  highlight?: string[];
  shadeOnly?: boolean;
  legend?: PointTone[];
  height?: number;
  labels?: boolean;
  initialView?: View;
  footnote?: ReactNode;
}) {
  const [view, setView] = useState<View>(table ? initialView : "map");
  const [selected, setSelected] = useState<string | null>(null);

  const body =
    view === "map" || !table ? (
      <AddressMap
        points={points}
        highlight={highlight}
        shadeOnly={shadeOnly}
        selectedId={selected}
        onSelect={setSelected}
        legend={legend}
        labels={labels}
        height={height}
      />
    ) : (
      table
    );

  return (
    <div className="space-y-4">
      {(title || table) && (
        <SectionTitle
          title={title ?? ""}
          hint={hint}
          right={
            table ? <ViewToggle view={view} onChange={setView} label={title ?? "View"} /> : undefined
          }
        />
      )}
      {body}
      {footnote && (
        <p className="text-sm" style={{ color: "var(--faint)" }}>
          {footnote}
        </p>
      )}
    </div>
  );
}
