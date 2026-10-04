"use client";

import { useMemo, useState } from "react";
import { LazyAddressMap as AddressMap } from "@/components/map/lazy-map";
import { ViewToggle, type View } from "@/components/view-toggle";
import {
  Card,
  ChangeStatusBadge,
  JurisdictionBadge,
  SectionTitle,
  Table,
  Td,
  Th,
} from "@/components/ui";
import type { MapPoint } from "@/components/map/model";
import { statusOf } from "@/lib/changes";
import { placeable } from "./filters";
import type { AddressRecord, ChangeTest, ChangeTestResult } from "@/lib/types";

/** The five change cases, and the buildings each one moves.
 *
 * This is the view where a map earns its place most clearly. "T3 affects 184
 * addresses" is a number; the same answer drawn shows at a glance that they are
 * all in one city, which is the thing a reader actually wants to know about a
 * law change and the thing a count cannot tell them.
 *
 * Selecting a case is the only control. Everything else follows from it — the
 * pins, the table, the highlighted jurisdictions — because a change case is a
 * single question and filtering inside one would answer a different one.
 *
 * A case whose affected buildings have no coordinates still reports its count:
 * the map says how many it could not draw rather than quietly showing fewer
 * pins than the number beside it.
 */

export function ChangesExplorer({
  tests,
  results,
  addresses,
}: {
  tests: ChangeTest[];
  results: ChangeTestResult[];
  /** Only the addresses some case touches — the whole sample is not needed. */
  addresses: AddressRecord[];
}) {
  const resultsById = useMemo(
    () => new Map(results.map((result) => [result.test_id, result])),
    [results],
  );
  // Open on a case that actually moved something. A case with an empty result
  // is a legitimate answer, but landing on one means the page opens with
  // nothing drawn and nothing to read. A blocked case has no answer at all.
  const opening =
    tests.find((test) => {
      const ran = resultsById.get(test.test_id);
      return ran && statusOf(ran) !== "blocked" && ran.affected_address_ids.length > 0;
    }) ?? tests.find((test) => resultsById.has(test.test_id));
  const [selected, setSelected] = useState<string>(opening?.test_id ?? "");
  const [view, setView] = useState<View>("map");
  const [pin, setPin] = useState<string | null>(null);

  const byId = useMemo(
    () => new Map(addresses.map((address) => [address.address_id, address])),
    [addresses],
  );

  const result = selected ? resultsById.get(selected) : undefined;
  const isBlocked = result ? statusOf(result) === "blocked" : false;
  const test = tests.find((item) => item.test_id === selected);

  const affected = useMemo(() => {
    if (!result) return [];
    return result.affected_address_ids
      .map((id) => byId.get(id))
      .filter((address): address is AddressRecord => Boolean(address));
  }, [result, byId]);

  const conflicted = useMemo(
    () => new Set(result?.conflict_flag_address_ids ?? []),
    [result],
  );

  const points = useMemo<MapPoint[]>(
    () =>
      affected.filter(placeable).map((address) => ({
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
        tone: conflicted.has(address.address_id) ? "conflict" : "affected",
        detail: [
          address.year_built ? `Built ${address.year_built}` : "Year built not in the record",
          address.units ? `${address.units} units` : "Unit count not in the record",
          conflicted.has(address.address_id)
            ? "Flagged for review on this case"
            : "Answer moves cleanly",
        ],
      })),
    [affected, conflicted],
  );

  /** Which cities the case lands in, so the map can shade their extent. */
  const cities = useMemo(
    () => [...new Set(affected.map((a) => a.legal_city).filter((c): c is string => Boolean(c)))],
    [affected],
  );

  const undrawable = affected.length - points.length;
  const missingRows = (result?.affected_address_ids.length ?? 0) - affected.length;

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Change cases"
        hint="Each case is answered by replaying Module B's evaluator at the relevant dates — not by hard-coding the expected outcome. Pick one to see which buildings it moves."
        right={<ViewToggle view={view} onChange={setView} label="Affected addresses view" />}
      />

      <div className="chips">
        {tests.map((item) => {
          const ran = resultsById.get(item.test_id);
          return (
            <button
              key={item.test_id}
              type="button"
              className="chip"
              aria-pressed={selected === item.test_id}
              onClick={() => {
                setSelected(item.test_id);
                setPin(null);
              }}
            >
              <span className="mono">{item.test_id}</span> {item.title}
              <span className="chip-count">
                {!ran
                  ? "not run"
                  : statusOf(ran) === "blocked"
                    ? "not computed"
                    : ran.affected_address_ids.length}
              </span>
            </button>
          );
        })}
      </div>

      {test && (
        <Card>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="mono text-sm font-semibold">{test.test_id}</span>
            <span className="font-medium">{test.title}</span>
            <span
              className="rounded-full border px-2 py-0.5 text-xs"
              style={{ borderColor: "var(--line)", color: "var(--faint)" }}
            >
              {test.type}
            </span>
            {result && <ChangeStatusBadge status={statusOf(result)} />}
            {result && (
              <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                as of {result.as_of}
              </span>
            )}
          </div>
          <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
            <strong>Expected:</strong> {test.expected_behavior}
          </p>
          {result && isBlocked ? (
            <p className="mt-2 text-sm">
              <strong style={{ color: "var(--critical)" }}>Not computed.</strong>{" "}
              {result.blocked_reason}
            </p>
          ) : result ? (
            <>
              <p className="mt-2 text-sm">{result.notes}</p>
              {(result.warnings ?? []).length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-xs" style={{ color: "var(--warn)" }}>
                  {(result.warnings ?? []).map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="mt-3 text-sm" style={{ color: "var(--faint)" }}>
              Not run yet —{" "}
              <code className="mono">
                POST /api/change-tracking/tests/{test.test_id}/run
              </code>
            </p>
          )}
        </Card>
      )}

      {result && isBlocked && (
        <p className="text-sm" style={{ color: "var(--faint)" }}>
          This case could not be computed, so there are no buildings to draw. That
          is not a finding that nothing moved — it is left out of changes.json
          until the missing input above exists.
        </p>
      )}

      {result && !isBlocked && (
        <>
          <div className="result-line">
            <span>
              <strong className="tabular-nums">{result.affected_address_ids.length}</strong>{" "}
              {result.affected_address_ids.length === 1 ? "building" : "buildings"} moved
            </span>
            {result.conflict_flag_address_ids.length > 0 && (
              <span style={{ color: "var(--warn)" }}>
                {result.conflict_flag_address_ids.length} flagged for review
              </span>
            )}
            {cities.length > 0 && (
              <span>
                in {cities.length} {cities.length === 1 ? "city" : "cities"}:{" "}
                {cities.slice(0, 4).join(", ")}
                {cities.length > 4 ? ` and ${cities.length - 4} more` : ""}
              </span>
            )}
            {undrawable > 0 && (
              <span style={{ color: "var(--warn)" }}>
                {undrawable} have no coordinate and are not drawn
              </span>
            )}
            {missingRows > 0 && (
              <span style={{ color: "var(--warn)" }}>
                {missingRows} are not in the loaded sample
              </span>
            )}
          </div>

          {affected.length === 0 ? (
            <p className="text-sm" style={{ color: "var(--faint)" }}>
              This case moves no building in the sample. That is a result, not a
              failure — it is what the evaluator found at those dates.
            </p>
          ) : view === "map" ? (
            <AddressMap
              points={points}
              highlight={cities.slice(0, 6)}
              shadeOnly
              selectedId={pin}
              onSelect={setPin}
              legend={["affected", "conflict"]}
              height={500}
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>ID</Th>
                  <Th>Address</Th>
                  <Th>Legal city</Th>
                  <Th>Jurisdiction</Th>
                  <Th>Built</Th>
                  <Th>Units</Th>
                  <Th>On this case</Th>
                </tr>
              </thead>
              <tbody>
                {affected.map((address) => (
                  <tr
                    key={address.address_id}
                    className={`clickable${pin === address.address_id ? " picked-row" : ""}`}
                    onClick={() =>
                      setPin(pin === address.address_id ? null : address.address_id)
                    }
                  >
                    <Td className="mono">{address.address_id}</Td>
                    <Td>{address.street_address}</Td>
                    <Td>{address.legal_city ?? address.postal_city}</Td>
                    <Td>
                      <JurisdictionBadge status={address.jurisdiction_status} />
                    </Td>
                    <Td className="tabular-nums">{address.year_built ?? "—"}</Td>
                    <Td className="tabular-nums">{address.units ?? "—"}</Td>
                    <Td className="text-xs">
                      {conflicted.has(address.address_id) ? (
                        <span style={{ color: "var(--warn)", fontWeight: 600 }}>
                          flagged for review
                        </span>
                      ) : (
                        "moves cleanly"
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}
    </div>
  );
}
