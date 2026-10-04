"use client";

import { useMemo, useState } from "react";
import { clientApi } from "@/lib/client-api";
import type { Cardinality, ContractVocabulary, PlaceVocabulary } from "@/lib/roles";
import type { AddressRecord, Contract, Place } from "@/lib/types";
import { LazyAddressMap as AddressMap } from "./map/lazy-map";
import type { MapPoint } from "./map/model";
import { Contracts } from "./contracts";
import { Spinner } from "./google-mark";
import { JurisdictionBadge, ZipDiscrepancyBadge } from "./ui";

/** The addresses one person is watching — and what they are watching them for.
 *
 * Private to whoever is signed in, with nothing here that could name another
 * account: there is no sharing in this app, so the list is only ever your own.
 *
 * The same table backs three roles and the list reads differently in each,
 * because a renter's home, a provider's building and an advocate's case are
 * three different kinds of thing. What changes with the role is the
 * vocabulary, whether a name and a note are worth asking for, and above all
 * how many rows the page expects:
 *
 * - a renter has one, so the first is promoted and the rest are a footnote;
 * - a provider has a portfolio, so the list is flat and every row is equal;
 * - an advocate has independent cases, so they are numbered rather than summed.
 *
 * A housing agency never reaches this component. They hold no addresses of
 * their own — the whole stock on file is their subject — so `/places` sends them to
 * the map that already covers it.
 *
 * The search is over the address book this account was set up with, which
 * carries a year built and a unit count for every row. A renter may also keep
 * an address that is not in it, because their home is wherever they actually
 * live rather than wherever the import happened to reach; it is geocoded to
 * its legal city and saved with those two facts absent rather than guessed, so
 * the rules that turn on them answer "unknown" and name the field.
 *
 * The other three are not offered that, and the reason is not permission: a
 * provider's list is the buildings they are answerable for and an advocate's
 * is the matters they hold, and a row typed into either is one nobody
 * imported. Any address can still be *asked about* — that is the assistant on
 * the overview page, which answers without keeping anything.
 */
/** Whether what was typed is enough for the resolver: street, city, state.
 *
 * The same shape `parse_typed` requires on the server, checked here only so
 * the offer does not appear over something that would be refused. The server
 * is what decides. Mirrored in components/assistant/cards.tsx, which puts the
 * same offer inside the conversation. */
function looksLikeAddress(query: string): boolean {
  const parts = query
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part && !/^(?:usa|u\.s\.a\.|united states)$/i.test(part));
  return parts.length >= 3 && /^[a-z]{2}(\s*\d{5}(-\d{4})?)?$/i.test(parts[parts.length - 1]);
}

export function Places({
  initial,
  vocab,
  cardinality,
  contractVocab,
  contracts,
}: {
  initial: Place[];
  vocab: PlaceVocabulary;
  cardinality: Cardinality;
  contractVocab: ContractVocabulary;
  /** Every agreement the viewer holds, across all of their buildings. One
   *  request for the page rather than one per row. */
  contracts: Contract[];
}) {
  const [places, setPlaces] = useState(initial);
  const [pin, setPin] = useState<string | null>(null);
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<AddressRecord[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [error, setError] = useState("");

  // Only a renter, and only once a search of the book has come back empty:
  // offering it sooner would steer somebody past a building they already hold,
  // which carries the two facts a typed one cannot.
  const keepable =
    cardinality === "one" && hits !== null && hits.length === 0 && looksLikeAddress(query);

  async function keep() {
    setPending("typed");
    setError("");
    try {
      const place = await clientApi<Place>("/accounts/me/places/by-address", {
        method: "POST",
        body: JSON.stringify({ address: query.trim() }),
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

  /** Save the name and the note together.
   *
   * Both in one request because the API sets both from the body: sending one
   * alone would clear the other, so the form owns the pair.
   */
  async function describe(place: Place, label: string, note: string) {
    setPending(String(place.id));
    setError("");
    try {
      const updated = await clientApi<Place>(`/accounts/me/places/${place.id}`, {
        method: "PATCH",
        body: JSON.stringify({ label: label.trim() || null, note: note.trim() || null }),
      });
      setPlaces((current) => current.map((p) => (p.id === updated.id ? updated : p)));
      setEditing(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
    }
    setPending(null);
  }

  const saved = new Set(places.map((p) => p.address_id));
  const describable = Boolean(vocab.label || vocab.note);
  const filed = useMemo(() => {
    const grouped = new Map<number, Contract[]>();
    for (const contract of contracts) {
      grouped.set(contract.place_id, [...(grouped.get(contract.place_id) ?? []), contract]);
    }
    return grouped;
  }, [contracts]);

  // Only the buildings the geocoder actually placed. An unresolved address has
  // no coordinate and is not given an approximate one, so the map always shows
  // the count it drew rather than implying it drew them all.
  const points = useMemo<MapPoint[]>(
    () =>
      places
        .filter((place) => place.latitude !== null && place.longitude !== null)
        .map((place) => ({
          id: String(place.id),
          lon: place.longitude as number,
          lat: place.latitude as number,
          title: place.label ?? place.street_address,
          subtitle:
            place.jurisdiction_status === "resolved"
              ? `${place.legal_city}, ${place.legal_state ?? place.state}`
              : `${place.postal_city}, ${place.state} — mailing city`,
          jurisdiction: place.legal_city,
          state: place.state,
          tone: "mine",
          detail: [
            place.year_built ? `Built ${place.year_built}` : "Year built not in the record",
            place.units ? `${place.units} units` : "Unit count not in the record",
            place.contract_count
              ? `${place.contract_count} ${
                  place.contract_count === 1 ? contractVocab.one : contractVocab.many
                } filed`
              : `No ${contractVocab.one} filed`,
          ],
        })),
    [places, contractVocab],
  );
  const unplaced = places.length - points.length;
  // A renter's list is one home plus footnotes; everybody else's is flat.
  const [home, ...rest] = places;
  const primary = cardinality === "one" ? (home ? [home] : []) : places;
  const secondary = cardinality === "one" ? rest : [];

  return (
    <div className="space-y-6">
      <form onSubmit={search} className="flex flex-wrap items-end gap-2">
        <label className="min-w-56 flex-1 text-sm">
          <span style={{ color: "var(--muted)" }}>{vocab.search}</span>
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
            <div className="space-y-3 p-4">
              <p className="text-sm" style={{ color: "var(--faint)" }}>
                Nothing on file matches that. The street and the city are
                matched separately, so the two together find nothing.
              </p>
              {keepable && (
                <>
                  <p className="text-sm" style={{ color: "var(--muted)" }}>
                    It can be looked up and kept anyway. It gets its legal city
                    from the same geocoder as every other building here, and no
                    year built or unit count — so the rules that turn on those
                    will say so rather than guess.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={keep}
                    disabled={pending !== null}
                  >
                    {pending === "typed" && <Spinner />}
                    Use {query.trim()}
                  </button>
                </>
              )}
            </div>
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
                  <JurisdictionBadge status={hit.jurisdiction_status} />
                  {hit.zip_discrepancy && <ZipDiscrepancyBadge />}
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

      {points.length > 0 && (
        <div className="space-y-2">
          <AddressMap
            points={points}
            selectedId={pin}
            onSelect={setPin}
            legend={["mine"]}
            labels={points.length <= 12}
            height={cardinality === "one" ? 280 : 400}
          />
          {unplaced > 0 && (
            <p className="text-sm" style={{ color: "var(--warn)" }}>
              {unplaced} of your {places.length} could not be placed: the geocoder
              returned no coordinate, so there is no pin rather than an approximate one.
            </p>
          )}
        </div>
      )}

      <div className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">
          {cardinality === "one"
            ? "Your home"
            : `Your ${places.length === 1 ? vocab.one : vocab.many}`}
        </h2>

        {places.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--faint)" }}>
            Nothing saved yet. Search above to add one.
          </p>
        ) : (
          <>
            <ul className="space-y-3">
              {primary.map((place, index) => (
                <Row
                  key={place.id}
                  place={place}
                  index={cardinality === "caseload" ? index + 1 : null}
                  vocab={vocab}
                  contractVocab={contractVocab}
                  contracts={filed.get(place.id) ?? []}
                  describable={describable}
                  editing={editing === place.id}
                  busy={pending === String(place.id)}
                  filesOpen={opened.has(place.id)}
                  onToggleFiles={() => setOpened(flip(opened, place.id))}
                  onEdit={() => setEditing(editing === place.id ? null : place.id)}
                  onSave={(label, note) => describe(place, label, note)}
                  onRemove={() => remove(place)}
                />
              ))}
            </ul>

            {cardinality === "one" && secondary.length > 0 && (
              <div className="space-y-3 pt-4">
                <h3 className="text-sm font-semibold" style={{ color: "var(--muted)" }}>
                  Also watching
                </h3>
                <p className="text-sm" style={{ color: "var(--faint)" }}>
                  Kept, but the overview answers your home only. Remove the one
                  above to make a different address your home.
                </p>
                <ul className="space-y-3">
                  {secondary.map((place) => (
                    <Row
                      key={place.id}
                      place={place}
                      index={null}
                      vocab={vocab}
                      contractVocab={contractVocab}
                      contracts={filed.get(place.id) ?? []}
                      describable={describable}
                      editing={editing === place.id}
                      busy={pending === String(place.id)}
                      filesOpen={opened.has(place.id)}
                      onToggleFiles={() => setOpened(flip(opened, place.id))}
                      onEdit={() => setEditing(editing === place.id ? null : place.id)}
                      onSave={(label, note) => describe(place, label, note)}
                      onRemove={() => remove(place)}
                    />
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function flip(current: Set<number>, id: number): Set<number> {
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

function Row({
  place,
  index,
  vocab,
  contractVocab,
  contracts,
  describable,
  editing,
  busy,
  filesOpen,
  onToggleFiles,
  onEdit,
  onSave,
  onRemove,
}: {
  place: Place;
  index: number | null;
  vocab: PlaceVocabulary;
  contractVocab: ContractVocabulary;
  contracts: Contract[];
  describable: boolean;
  editing: boolean;
  busy: boolean;
  filesOpen: boolean;
  onToggleFiles: () => void;
  onEdit: () => void;
  onSave: (label: string, note: string) => void;
  onRemove: () => void;
}) {
  return (
    <li
      className="rounded-xl border p-4"
      style={{ borderColor: "var(--line)", background: "var(--surface)" }}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-48 flex-1">
          <div className="font-medium">
            {index !== null && (
              <span className="mr-2" style={{ color: "var(--faint)" }}>
                {index}.
              </span>
            )}
            {place.label ?? place.street_address}
          </div>
          {place.label && (
            <div className="text-sm" style={{ color: "var(--faint)" }}>
              {place.street_address}
            </div>
          )}
          <div className="text-sm" style={{ color: "var(--muted)" }}>
            {place.jurisdiction_status === "resolved"
              ? `${place.legal_city}, ${place.legal_state ?? place.state}`
              : `${place.postal_city}, ${place.state} (mailing city; legal city pending)`}
            {place.postal_city_differs && (
              <>
                {" · "}
                <span style={{ color: "var(--warn)" }}>mail says {place.postal_city}</span>
              </>
            )}
          </div>
          {place.note && !editing && (
            <p className="mt-2 max-w-xl text-sm" style={{ color: "var(--faint)" }}>
              {place.note}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <JurisdictionBadge status={place.jurisdiction_status} />
            {place.zip_discrepancy && <ZipDiscrepancyBadge />}
          </div>
        </div>
        <span className="mono text-xs" style={{ color: "var(--faint)" }}>
          {place.address_id}
        </span>
        <button className="btn btn-quiet" onClick={onToggleFiles}>
          {contractVocab.title}
          {contracts.length > 0 && (
            <span className="chip-count">{contracts.length}</span>
          )}
        </button>
        {describable && (
          <button className="btn btn-quiet" onClick={onEdit} disabled={busy}>
            {editing ? "Cancel" : place.label || place.note ? "Edit" : "Describe"}
          </button>
        )}
        <button className="btn btn-quiet" disabled={busy} onClick={onRemove}>
          Remove {busy && <Spinner size={13} />}
        </button>
      </div>

      {editing && describable && (
        <Describe place={place} vocab={vocab} busy={busy} onSave={onSave} />
      )}

      {filesOpen && (
        <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--line)" }}>
          <Contracts
            placeId={place.id}
            units={place.units}
            vocab={contractVocab}
            initial={contracts}
          />
        </div>
      )}
    </li>
  );
}

/** The name and the note, which only some roles are asked for.
 *
 * A renter does not need to label the building they live in; a provider with
 * eleven of them does, and an advocate needs somewhere to put what the matter
 * is actually about. The fields exist on every row — the vocabulary decides
 * whether anybody is offered them.
 */
function Describe({
  place,
  vocab,
  busy,
  onSave,
}: {
  place: Place;
  vocab: PlaceVocabulary;
  busy: boolean;
  onSave: (label: string, note: string) => void;
}) {
  const [label, setLabel] = useState(place.label ?? "");
  const [note, setNote] = useState(place.note ?? "");
  return (
    <form
      className="mt-4 space-y-3 border-t pt-4"
      style={{ borderColor: "var(--line)" }}
      onSubmit={(event) => {
        event.preventDefault();
        onSave(label, note);
      }}
    >
      {vocab.label && (
        <label className="block text-sm">
          <span style={{ color: "var(--muted)" }}>{vocab.label}</span>
          <input
            className="field mt-1"
            value={label}
            maxLength={120}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
      )}
      {vocab.note && (
        <label className="block text-sm">
          <span style={{ color: "var(--muted)" }}>{vocab.note}</span>
          <textarea
            className="field mt-1"
            rows={3}
            value={note}
            maxLength={2000}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      )}
      <button className="btn btn-primary" disabled={busy}>
        Save {busy && <Spinner />}
      </button>
    </form>
  );
}
