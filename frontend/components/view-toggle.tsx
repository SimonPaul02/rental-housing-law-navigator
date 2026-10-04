"use client";

/** Map or table, for the same rows.
 *
 * Both views always show exactly the same filtered set — the toggle changes
 * how it is drawn and nothing about what is in it. That matters for more than
 * taste: the table is the version that is readable with a screen reader,
 * searchable with the browser's own find, and printable, so the map is never
 * allowed to be the only way to reach something.
 *
 * A radio group rather than two buttons, because they are alternatives with
 * one answer and that is what a screen reader should hear.
 */

export type View = "map" | "table";

export function ViewToggle({
  view,
  onChange,
  label = "View",
}: {
  view: View;
  onChange: (next: View) => void;
  label?: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {(["map", "table"] as const).map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={view === option}
          className={`segment${view === option ? " active" : ""}`}
          onClick={() => onChange(option)}
        >
          {option === "map" ? "Map" : "Table"}
        </button>
      ))}
    </div>
  );
}
