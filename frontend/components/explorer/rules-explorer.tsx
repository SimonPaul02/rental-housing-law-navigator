"use client";

import { Fragment, useMemo, useState } from "react";
import { LazyAddressMap as AddressMap } from "@/components/map/lazy-map";
import { ViewToggle, type View } from "@/components/view-toggle";
import {
  Expand,
  FieldList,
  SectionTitle,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { MapPoint } from "@/components/map/model";
import { anchorFor, indexBy, type Anchor } from "@/lib/jurisdictions";
import type { RuleRecord, RuleStats } from "@/lib/types";

/** The rule record, filterable — and placed where its jurisdiction is.
 *
 * A rule has no coordinate; it has a jurisdiction. So the map draws one bubble
 * per jurisdiction, sized by how many rules are behind it, at the mean of the
 * sample addresses in that jurisdiction. That is a derived point and the view
 * says so: a corpus can cover a city the address sample never reaches, and
 * those rules are counted off to one side rather than quietly dropped — a map
 * that hides what it cannot draw makes coverage look better than it is.
 *
 * The table is the primary view here and stays the default. A rule is text:
 * twenty fields, a requirement, a quoted span. The map answers one question
 * well — where is the corpus thick and where is it thin — and the table
 * answers every other one.
 */

export interface RulesExplorerProps {
  rules: RuleRecord[];
  stats: RuleStats | null;
  /** Precomputed on the server; a Map does not survive serialisation. */
  anchors: Anchor[];
  initialJurisdiction?: string;
  initialCategory?: string;
}

export function RulesExplorer({
  rules,
  stats,
  anchors,
  initialJurisdiction = "",
  initialCategory = "",
}: RulesExplorerProps) {
  const [view, setView] = useState<View>("table");
  const [q, setQ] = useState("");
  const [level, setLevel] = useState("");
  const [category, setCategory] = useState(initialCategory);
  const [status, setStatus] = useState("");
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [jurisdiction, setJurisdiction] = useState(initialJurisdiction);

  // Rebuilt as a Map because a Map cannot be serialised across the boundary.
  const index = useMemo(() => indexBy(anchors), [anchors]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rules.filter((rule) => {
      if (level && rule.level !== level) return false;
      if (category && rule.category !== category) return false;
      if (status && rule.status !== status) return false;
      if (conflictsOnly && !rule.conflict_flag) return false;
      if (
        jurisdiction &&
        rule.jurisdiction.trim().toLowerCase() !== jurisdiction.trim().toLowerCase()
      ) {
        return false;
      }
      if (!needle) return true;
      return [rule.title, rule.requirement, rule.citation, rule.key_value ?? "", rule.team_rule_id]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [rules, q, level, category, status, conflictsOnly, jurisdiction]);

  /** One bubble per jurisdiction in the filtered set. */
  const { points, unplaced } = useMemo(() => {
    const tally = new Map<string, { count: number; conflicts: number }>();
    for (const rule of shown) {
      const key = rule.jurisdiction.trim();
      const entry = tally.get(key) ?? { count: 0, conflicts: 0 };
      entry.count += 1;
      if (rule.conflict_flag) entry.conflicts += 1;
      tally.set(key, entry);
    }
    const drawn: MapPoint[] = [];
    let missing = 0;
    for (const [name, entry] of tally) {
      const anchor = anchorFor(name, index);
      if (!anchor) {
        missing += entry.count;
        continue;
      }
      drawn.push({
        // Keyed by the corpus spelling, because that is what the filter
        // compares against; titled by the anchor, because "California" reads
        // better on a map than "CA".
        id: name,
        lon: anchor.lon,
        lat: anchor.lat,
        title: anchor.name,
        subtitle: `${entry.count} ${entry.count === 1 ? "rule" : "rules"} · ${anchor.level}-level`,
        jurisdiction: name,
        state: "",
        tone: entry.conflicts > 0 ? "conflict" : "neutral",
        weight: entry.count,
        labelAnchor: anchor.level === "state" ? "bottom" : "top",
        detail: [
          `Drawn at the mean of the ${anchor.addresses} sample ${
            anchor.addresses === 1 ? "address" : "addresses"
          } we hold here — not this jurisdiction's own centre`,
          entry.conflicts > 0
            ? `${entry.conflicts} flagged as conflicting`
            : "No conflicts flagged",
        ],
      });
    }
    return { points: drawn, unplaced: missing };
  }, [shown, index]);

  const facets = useMemo(() => {
    const tally = new Map<string, number>();
    for (const rule of shown) {
      tally.set(rule.jurisdiction, (tally.get(rule.jurisdiction) ?? 0) + 1);
    }
    return [...tally.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [shown]);

  const allJurisdictions = useMemo(
    () =>
      Object.entries(stats?.by_jurisdiction ?? {})
        .map(([name, count]) => ({ name, count }))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    [stats],
  );
  // The map and the URL both hand back a corpus spelling; the menu has to
  // match one of its own options to show it as chosen, so it is resolved
  // against the option list rather than trusted verbatim.
  const chosenJurisdiction =
    allJurisdictions.find(
      ({ name }) => name.toLowerCase() === jurisdiction.trim().toLowerCase(),
    )?.name ?? "";
  const categories = Object.keys(stats?.by_category ?? {}).sort();
  const statuses = Object.keys(stats?.by_status ?? {}).sort();
  const active = Boolean(q || level || category || status || conflictsOnly || jurisdiction);

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Rule records"
        hint="Every record was kept only because its quoted span was found verbatim in its source document. Expand a row to read the whole record, fields and all."
        right={<ViewToggle view={view} onChange={setView} label="Rule view" />}
      />

      <div className="filters">
        <div className="filter-row">
          <label className="filter-field" style={{ flex: "1 1 240px" }}>
            <span>Search</span>
            <input
              className="field"
              value={q}
              placeholder="Requirement, title, citation or id"
              onChange={(event) => setQ(event.target.value)}
            />
          </label>
          <label className="filter-field">
            <span>Jurisdiction</span>
            <select
              className="field"
              value={chosenJurisdiction}
              onChange={(e) => setJurisdiction(e.target.value)}
            >
              <option value="">Every jurisdiction</option>
              {allJurisdictions.map(({ name, count }) => (
                <option key={name} value={name}>
                  {name} ({count})
                </option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>Level</span>
            <select className="field" value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="">State and city</option>
              <option value="state">State</option>
              <option value="city">City</option>
            </select>
          </label>
          <label className="filter-field">
            <span>Category</span>
            <select
              className="field"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">Every category</option>
              {categories.map((name) => (
                <option key={name} value={name}>
                  {name.replace(/_/g, " ")} ({stats?.by_category[name]})
                </option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>Status</span>
            <select className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any status</option>
              {statuses.map((name) => (
                <option key={name} value={name}>
                  {name.replace(/_/g, " ")} ({stats?.by_status[name]})
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* What is left here is not a choice of one value out of forty — it is
            a toggle and an escape hatch, and both read better as chips than as
            another menu with two entries in it. */}
        <div className="filter-row">
          <div className="chips">
            <button
              type="button"
              className="chip"
              aria-pressed={conflictsOnly}
              onClick={() => setConflictsOnly(!conflictsOnly)}
            >
              Conflicts only <span className="chip-count">{stats?.flagged_conflicts ?? 0}</span>
            </button>
            {active && (
              <button
                type="button"
                className="chip"
                onClick={() => {
                  setQ("");
                  setLevel("");
                  setCategory("");
                  setStatus("");
                  setConflictsOnly(false);
                  setJurisdiction("");
                }}
              >
                Clear all
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="result-line">
        <span>
          <strong className="tabular-nums">{shown.length}</strong> of {rules.length} records
        </span>
        <span>
          {facets.length} {facets.length === 1 ? "jurisdiction" : "jurisdictions"}
        </span>
        {unplaced > 0 && (
          <span style={{ color: "var(--warn)" }}>
            {unplaced} in jurisdictions the address sample does not reach — not on the map
          </span>
        )}
      </div>

      {view === "map" ? (
        <>
          <AddressMap
            points={points}
            sizeBy="weight"
            labels
            legend={["neutral", "conflict"]}
            selectedId={jurisdiction || null}
            onSelect={(id) => setJurisdiction(id ?? "")}
            height={480}
          />
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Each bubble is one jurisdiction, its area proportional to how many
            of the filtered rules belong to it, drawn at the mean coordinate of
            the sample addresses there. It marks where the corpus is thick and
            where it is thin — it is not a boundary and not a city centre.
            Click one to filter the table to it. A state bubble sits at the
            centre of that state&rsquo;s sample, which can land on one of its
            own cities: every Massachusetts address here is in Boston or
            Cambridge, so Massachusetts and Boston genuinely coincide. Zoom in,
            or filter by level, to separate overlapping labels.
          </p>
        </>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Jurisdiction</Th>
              <Th>Category</Th>
              <Th>Status</Th>
              <Th>Requirement</Th>
              <Th>Citation</Th>
              <Th>Conflict</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((rule) => (
              <Fragment key={rule.team_rule_id}>
                <tr>
                  <Td className="mono whitespace-nowrap">{rule.team_rule_id}</Td>
                  <Td className="whitespace-nowrap">
                    {rule.jurisdiction}
                    <div className="text-xs" style={{ color: "var(--faint)" }}>
                      {rule.level}
                    </div>
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{rule.category}</Td>
                  <Td>
                    <StatusBadge status={rule.status} />
                    {rule.effective_date && (
                      <div className="mt-1 text-xs mono" style={{ color: "var(--faint)" }}>
                        {rule.effective_date}
                      </div>
                    )}
                  </Td>
                  <Td>
                    <div className="max-w-md">{rule.requirement}</div>
                    {rule.key_value && (
                      <div className="mt-1 text-xs font-medium">{rule.key_value}</div>
                    )}
                  </Td>
                  <Td>
                    <a
                      href={rule.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:underline"
                      style={{ color: "var(--accent)" }}
                    >
                      {rule.citation}
                    </a>
                  </Td>
                  <Td className="whitespace-nowrap text-xs">
                    {rule.conflict_flag ? (
                      <span style={{ color: "var(--accent-ink)", fontWeight: 600 }}>flagged</span>
                    ) : (
                      <span style={{ color: "var(--faint)" }}>none</span>
                    )}
                  </Td>
                </tr>
                <tr>
                  <td colSpan={7} style={{ paddingTop: 0 }}>
                    <Expand label="All fields">
                      <FieldList fields={ruleFields(rule)} />
                    </Expand>
                  </td>
                </tr>
              </Fragment>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={7} className="px-5 py-8 text-center text-sm" style={{ color: "var(--muted)" }}>
                  No record matches those filters.
                </td>
              </tr>
            )}
          </tbody>
        </Table>
      )}
    </div>
  );
}

/** `coverage_conditions` is a string on most records and an object on a few, so
 *  it is rendered rather than assumed. */
function asText(value: string | Record<string, unknown> | null): string | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

/** Every field of a rule record, in the order the submission schema lists them,
 *  so this reads as the record itself rather than a curated summary of it. */
function ruleFields(rule: RuleRecord): [string, React.ReactNode][] {
  return [
    ["Team rule id", <span className="mono">{rule.team_rule_id}</span>],
    ["Title", rule.title],
    ["Jurisdiction", `${rule.jurisdiction} (${rule.level})`],
    ["Category", rule.category],
    ["Status", rule.status],
    ["Key value", rule.key_value],
    ["Requirement", rule.requirement],
    ["Coverage conditions", asText(rule.coverage_conditions)],
    ["Exemptions", rule.exemptions],
    ["Overrides", rule.overrides?.length ? rule.overrides.join(", ") : null],
    ["Interaction", rule.interaction],
    ["Effective date", rule.effective_date],
    ["Citation", rule.citation],
    ["Confidence", rule.confidence === null ? null : rule.confidence.toFixed(2)],
    ["Conflict flag", rule.conflict_flag ? "true" : "false"],
    ["Conflict note", rule.conflict_note],
    ["Source document", <span className="mono">{rule.source_doc_id}</span>],
    [
      "Source url",
      <a
        href={rule.source_url}
        target="_blank"
        rel="noreferrer"
        className="hover:underline"
        style={{ color: "var(--accent)" }}
      >
        {rule.source_url}
      </a>,
    ],
    [
      "Quoted span",
      <blockquote
        style={{
          borderLeft: "2px solid var(--line)",
          paddingLeft: 12,
          margin: 0,
          fontStyle: "italic",
        }}
      >
        {rule.quoted_span}
      </blockquote>,
    ],
  ];
}
