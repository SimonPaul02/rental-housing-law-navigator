"use client";

import { useState } from "react";
import { clientApi, clientUpload } from "@/lib/client-api";
import { bytes, money, term } from "@/lib/format";
import type { ContractVocabulary } from "@/lib/roles";
import type { Contract } from "@/lib/types";
import { Spinner } from "./google-mark";

/** The tenancy agreements filed against one building.
 *
 * Four roles, one component, and the difference is again how many there are.
 * A renter has one lease for one home and is never asked which unit it covers
 * — that would be asking them to restate their own address. A provider's
 * thirty-two unit building has thirty-two agreements and the unit is the key,
 * so it leads the form and orders the list.
 *
 * What this does *not* do is read the file. The rent and the term are typed in
 * by the person who uploaded it, are never verified, and are never used in a
 * calculation: they are shown beside the rule that governs them so a human can
 * compare the two. Extracting a rent from a PDF and then telling somebody what
 * they are owed would be advice given on an unverified reading of their own
 * document, which is the one thing an app like this must not do.
 */

const MAX_MB = 12;

export function Contracts({
  placeId,
  units,
  vocab,
  initial,
}: {
  placeId: number;
  /** The building's unit count, when the record has one. */
  units: number | null;
  vocab: ContractVocabulary;
  initial: Contract[];
}) {
  const [contracts, setContracts] = useState(initial);
  const [open, setOpen] = useState(initial.length === 0);
  const [busy, setBusy] = useState<number | "upload" | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [error, setError] = useState("");

  async function upload(form: FormData) {
    setBusy("upload");
    setError("");
    try {
      const created = await clientUpload<Contract>(
        `/accounts/me/places/${placeId}/contracts`,
        form,
      );
      setContracts((current) =>
        current.some((c) => c.id === created.id)
          ? current.map((c) => (c.id === created.id ? created : c))
          : [...current, created],
      );
      setOpen(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The upload did not go through.");
    }
    setBusy(null);
  }

  async function save(contract: Contract, patch: Partial<Contract>) {
    setBusy(contract.id);
    setError("");
    try {
      const updated = await clientApi<Contract>(`/accounts/me/contracts/${contract.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          unit_label: patch.unit_label ?? null,
          starts_on: patch.starts_on ?? null,
          ends_on: patch.ends_on ?? null,
          monthly_rent_cents: patch.monthly_rent_cents ?? null,
          note: patch.note ?? null,
        }),
      });
      setContracts((current) => current.map((c) => (c.id === updated.id ? updated : c)));
      setEditing(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
    }
    setBusy(null);
  }

  async function remove(contract: Contract) {
    setBusy(contract.id);
    setError("");
    try {
      await clientApi(`/accounts/me/contracts/${contract.id}`, { method: "DELETE" });
      setContracts((current) => current.filter((c) => c.id !== contract.id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not delete that.");
    }
    setBusy(null);
  }

  // A renter with one lease never sees the unit field; a renter who files a
  // second one does, because by then the two have to be told apart.
  const askUnit = vocab.perUnit || contracts.length >= 1;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h3 className="font-medium">{vocab.title}</h3>
          <p className="mt-0.5 max-w-xl text-sm" style={{ color: "var(--faint)" }}>
            {vocab.lede}
          </p>
        </div>
        <button type="button" className="btn btn-quiet" onClick={() => setOpen(!open)}>
          {open ? "Cancel" : vocab.add}
        </button>
      </div>

      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}

      {contracts.length > 0 && (
        <ul className="space-y-2">
          {contracts.map((contract) => (
            <li key={contract.id} className="contract-row">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <div className="min-w-48 flex-1">
                  <div className="font-medium">
                    {contract.unit_label ? (
                      <>
                        <span className="unit-tag">{contract.unit_label}</span>{" "}
                        {contract.filename}
                      </>
                    ) : (
                      contract.filename
                    )}
                  </div>
                  <div className="mt-0.5 text-xs" style={{ color: "var(--faint)" }}>
                    {contract.kind}
                    {contract.page_count ? ` · ${contract.page_count} pages` : ""}
                    {" · "}
                    {bytes(contract.byte_size)}
                    {" · uploaded "}
                    {contract.created_at.slice(0, 10)}
                  </div>
                  <div className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
                    {term(contract) ?? (
                      <span style={{ color: "var(--faint)" }}>no term recorded</span>
                    )}
                    {contract.monthly_rent_cents !== null && (
                      <> · {money(contract.monthly_rent_cents)} a month</>
                    )}
                  </div>
                  {contract.note && (
                    <p className="mt-1 max-w-xl text-sm" style={{ color: "var(--faint)" }}>
                      {contract.note}
                    </p>
                  )}
                </div>
                <a
                  className="btn btn-quiet"
                  href={`/api/accounts/me/contracts/${contract.id}/file`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open
                </a>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => setEditing(editing === contract.id ? null : contract.id)}
                >
                  {editing === contract.id ? "Cancel" : "Details"}
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={busy === contract.id}
                  onClick={() => remove(contract)}
                >
                  Delete {busy === contract.id && <Spinner size={13} />}
                </button>
              </div>

              {editing === contract.id && (
                <DetailsForm
                  contract={contract}
                  askUnit={askUnit}
                  units={units}
                  busy={busy === contract.id}
                  onSave={(patch) => save(contract, patch)}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      {open && (
        <UploadForm
          askUnit={askUnit}
          units={units}
          busy={busy === "upload"}
          onSubmit={upload}
        />
      )}

      {!open && contracts.length === 0 && (
        <p className="text-sm" style={{ color: "var(--faint)" }}>
          Nothing filed yet.
        </p>
      )}
    </div>
  );
}

function UploadForm({
  askUnit,
  units,
  busy,
  onSubmit,
}: {
  askUnit: boolean;
  units: number | null;
  busy: boolean;
  onSubmit: (form: FormData) => void;
}) {
  const [rent, setRent] = useState("");

  return (
    <form
      className="contract-form"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        // The field is in dollars because that is what a lease says; the API
        // stores cents, because a rent in floating point is a rent that drifts.
        const dollars = Number.parseFloat(String(form.get("rent") ?? ""));
        form.delete("rent");
        if (Number.isFinite(dollars) && dollars >= 0) {
          form.set("monthly_rent_cents", String(Math.round(dollars * 100)));
        }
        for (const key of ["unit_label", "starts_on", "ends_on", "note"]) {
          if (!String(form.get(key) ?? "").trim()) form.delete(key);
        }
        onSubmit(form);
      }}
    >
      <label className="filter-field" style={{ flex: "1 1 240px" }}>
        <span>The file — PDF, photo or Word, up to {MAX_MB} MB</span>
        <input
          className="field"
          type="file"
          name="file"
          required
          accept=".pdf,.png,.jpg,.jpeg,.heic,.webp,.txt,.doc,.docx"
        />
      </label>

      <div className="filter-row">
        {askUnit && (
          <label className="filter-field">
            <span>Unit{units ? ` — the record says ${units} in this building` : ""}</span>
            <input className="field" name="unit_label" maxLength={64} placeholder="4B" />
          </label>
        )}
        <label className="filter-field">
          <span>Starts</span>
          <input className="field" type="date" name="starts_on" />
        </label>
        <label className="filter-field">
          <span>Ends</span>
          <input className="field" type="date" name="ends_on" />
        </label>
        <label className="filter-field">
          <span>Rent a month, in dollars</span>
          <input
            className="field"
            name="rent"
            inputMode="decimal"
            value={rent}
            placeholder="1850"
            onChange={(event) => setRent(event.target.value.replace(/[^\d.]/g, ""))}
          />
        </label>
      </div>

      <label className="filter-field">
        <span>Note</span>
        <input className="field" name="note" maxLength={2000} />
      </label>

      <p className="text-xs" style={{ color: "var(--faint)" }}>
        The term and the rent are yours to state and are never checked against
        the file. Nothing here is used to compute an answer — they are shown
        beside the rule that governs them so you can compare the two yourself.
      </p>

      <div>
        <button className="btn btn-primary" disabled={busy}>
          Upload {busy && <Spinner />}
        </button>
      </div>
    </form>
  );
}

function DetailsForm({
  contract,
  askUnit,
  units,
  busy,
  onSave,
}: {
  contract: Contract;
  askUnit: boolean;
  units: number | null;
  busy: boolean;
  onSave: (patch: Partial<Contract>) => void;
}) {
  const [unit, setUnit] = useState(contract.unit_label ?? "");
  const [starts, setStarts] = useState(contract.starts_on ?? "");
  const [ends, setEnds] = useState(contract.ends_on ?? "");
  const [rent, setRent] = useState(
    contract.monthly_rent_cents === null ? "" : (contract.monthly_rent_cents / 100).toString(),
  );
  const [note, setNote] = useState(contract.note ?? "");

  return (
    <form
      className="contract-form"
      onSubmit={(event) => {
        event.preventDefault();
        const dollars = Number.parseFloat(rent);
        onSave({
          unit_label: unit.trim() || null,
          starts_on: starts || null,
          ends_on: ends || null,
          monthly_rent_cents:
            Number.isFinite(dollars) && dollars >= 0 ? Math.round(dollars * 100) : null,
          note: note.trim() || null,
        });
      }}
    >
      <div className="filter-row">
        {askUnit && (
          <label className="filter-field">
            <span>Unit{units ? ` of ${units}` : ""}</span>
            <input
              className="field"
              value={unit}
              maxLength={64}
              onChange={(event) => setUnit(event.target.value)}
            />
          </label>
        )}
        <label className="filter-field">
          <span>Starts</span>
          <input
            className="field"
            type="date"
            value={starts}
            onChange={(event) => setStarts(event.target.value)}
          />
        </label>
        <label className="filter-field">
          <span>Ends</span>
          <input
            className="field"
            type="date"
            value={ends}
            onChange={(event) => setEnds(event.target.value)}
          />
        </label>
        <label className="filter-field">
          <span>Rent a month, in dollars</span>
          <input
            className="field"
            inputMode="decimal"
            value={rent}
            onChange={(event) => setRent(event.target.value.replace(/[^\d.]/g, ""))}
          />
        </label>
      </div>
      <label className="filter-field">
        <span>Note</span>
        <input
          className="field"
          value={note}
          maxLength={2000}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="flex items-center gap-3">
        <button className="btn btn-primary" disabled={busy}>
          Save {busy && <Spinner />}
        </button>
        <span className="mono text-xs" style={{ color: "var(--faint)" }}>
          sha256 {contract.sha256.slice(0, 12)}…
        </span>
      </div>
    </form>
  );
}
