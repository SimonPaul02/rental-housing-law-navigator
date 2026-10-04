"use client";

import { useState } from "react";
import { clientApi } from "@/lib/client-api";
import type { AddressRecord, Place } from "@/lib/types";
import { Spinner } from "./google-mark";

/** The buildings one person is watching.
 *
 * Private to whoever is signed in, with nothing here that could name another
 * account - there is no sharing in this app, so the list is only ever your
 * own.
 *
 * Addresses are picked from the sample rather than typed free-hand, and that
 * is a real limit rather than a shortcut: an answer needs the year built and
 * the unit count, and the 500 sample rows are the only buildings those facts
 * exist for. A typed address would resolve to a jurisdiction and then answer
 * "unknown" to nearly every condition, which looks like a broken app rather
 * than like missing data.
 */
export function Places({
  initial,
  noun,
}: {
  initial: Place[];
  noun: { one: string; many: string; add: string };
}) {
  const [places, setPlaces] = useState(initial);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<AddressRecord[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const needle = query.trim();
    if (needle.length < 2) return;
    setSearching(true);
    setError("");
    try {
      setHits(
        await clientApi<AddressRecord[]>(
          `/address-lookup/addresses?q=${encodeURIComponent(needle)}&limit=12`,
        ),
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Search failed.");
    }
    setSearching(false);
  }

  async function add(address: AddressRecord) {
    setPending(address.address_id);
    setError("");
    try {
      const place = await clientApi<Place>("/accounts/me/places", {
        method: "POST",
        body: JSON.stringify({ address_id: address.address_id }),
      });
      setPlaces((current) =>
        current.some((p) => p.id === place.id) ? current : [...current, place],
      );
      setHits(null);
      setQuery("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
    }
    setPending(null);
  }

  async function remove(place: Place) {
    setPending(String(place.id));
    setError("");
    try {
      await clientApi(`/accounts/me/places/${place.id}`, { method: "DELETE" });
      setPlaces((current) => current.filter((p) => p.id !== place.id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not remove that.");
    }
    setPending(null);
  }

  const saved = new Set(places.map((p) => p.address_id));

  return (
    <div className="space-y-6">
      <form onSubmit={search} className="flex flex-wrap items-end gap-2">
        <label className="min-w-56 flex-1 text-sm">
          <span style={{ color: "var(--muted)" }}>
            Find an address — street or city
          </span>
          <input
            className="field mt-1"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Dorchester, or 12 Elm"
          />
        </label>
        <button className="btn" disabled={searching || query.trim().length < 2}>
          Search {searching && <Spinner />}
        </button>
      </form>

      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}

      {hits !== null && (
        <div
          className="rounded-xl border"
          style={{ borderColor: "var(--line)", background: "var(--surface)" }}
        >
          {hits.length === 0 ? (
            <p className="p-4 text-sm" style={{ color: "var(--faint)" }}>
              Nothing in the sample matches that.
            </p>
          ) : (
            <ul>
              {hits.map((hit) => (
                <li
                  key={hit.address_id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3 last:border-0"
                  style={{ borderColor: "var(--line)" }}
                >
                  <span className="flex-1">
                    <span className="font-medium">{hit.street_address}</span>
                    <span className="ml-2 text-sm" style={{ color: "var(--muted)" }}>
                      {hit.postal_city}, {hit.state}
                    </span>
                  </span>
                  <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                    {hit.year_built ?? "year ?"} · {hit.units ?? "units ?"}
                  </span>
                  <button
                    className="btn"
                    disabled={saved.has(hit.address_id) || pending === hit.address_id}
                    onClick={() => add(hit)}
                  >
                    {saved.has(hit.address_id) ? "Saved" : "Save"}
                    {pending === hit.address_id && <Spinner />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">
          Your {places.length === 1 ? noun.one : noun.many}
        </h2>
        {places.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Nothing saved yet. Search above to add one.
          </p>
        ) : (
          <ul className="space-y-3">
            {places.map((place) => (
              <li
                key={place.id}
                className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border p-4"
                style={{ borderColor: "var(--line)", background: "var(--surface)" }}
              >
                <div className="min-w-48 flex-1">
                  <div className="font-medium">{place.street_address}</div>
                  <div className="text-sm" style={{ color: "var(--muted)" }}>
                    {place.legal_city ?? place.postal_city}, {place.legal_state ?? place.state}
                    {place.postal_city_differs && (
                      <>
                        {" · "}
                        <span style={{ color: "var(--warn)" }}>
                          mail says {place.postal_city}
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <span className="mono text-xs" style={{ color: "var(--faint)" }}>
                  {place.address_id}
                </span>
                <button
                  className="btn btn-quiet"
                  disabled={pending === String(place.id)}
                  onClick={() => remove(place)}
                >
                  Remove {pending === String(place.id) && <Spinner size={13} />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
