"use client";

import { useState } from "react";
import Link from "next/link";
import { clientApi, clientUpload } from "@/lib/client-api";
import type { AddressRecord, Contract, Place } from "@/lib/types";
import { Spinner } from "../google-mark";
import type {
  AddBuildingCard,
  AgreementDetailsCard,
  Card,
  DocumentCard,
  PickBuildingCard,
  Resolution,
  ViewCard,
} from "./model";

/** The controls the agent renders when it needs something.
 *
 * Each one does its work against the endpoint that already exists — the same
 * `POST /accounts/me/places` the buildings page uses, the same multipart
 * contracts route — and then hands back a `Resolution` naming what it did.
 * There is no write path through the chat: one for a building and one for a
 * document, both already authorised, and the agent is told what the database
 * says rather than what this component claims.
 *
 * So a card is not a form the model invented. It is the app's own control,
 * placed in the conversation at the point somebody needs it, which is the
 * difference between an assistant and a chat window beside a dashboard.
 */

export function CardView({
  card,
  onDone,
  busy,
}: {
  card: Card;
  /** Report what happened, which resumes the agent's turn. */
  onDone: (resolution: Resolution) => void;
  /** True while a turn is running, so a control cannot be answered twice. */
  busy: boolean;
}) {
  return (
    <div className="chat-card">
      {card.message && <p className="chat-card-ask">{card.message}</p>}
      <Control card={card} onDone={onDone} busy={busy} />
    </div>
  );
}

function Control({
  card,
  onDone,
  busy,
}: {
  card: Card;
  onDone: (resolution: Resolution) => void;
  busy: boolean;
}) {
  switch (card.kind) {
    case "ask_to_add_building":
      return <AddBuilding card={card} onDone={onDone} busy={busy} />;
    case "ask_to_pick_building":
      return <PickBuilding card={card} onDone={onDone} busy={busy} />;
    case "ask_for_document":
      return <UploadDocument card={card} onDone={onDone} busy={busy} />;
    case "ask_for_agreement_details":
      return <AgreementDetails card={card} onDone={onDone} busy={busy} />;
    case "show_view":
      return <ViewLink card={card} />;
  }
}

/* --------------------------------------------------------- add a building */

/** Search the sample and save one.
 *
 * Addresses come from the 500-row sample rather than being typed, and that is
 * a real limit rather than a shortcut: an answer needs the year built and the
 * unit count, and those exist only for these rows. A hand-typed address would
 * resolve to a jurisdiction and then answer "unknown" to almost every
 * condition, which reads as a broken app rather than as missing data.
 */
function AddBuilding({
  card,
  onDone,
  busy,
}: {
  card: AddBuildingCard;
  onDone: (resolution: Resolution) => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState(card.query ?? "");
  const [hits, setHits] = useState<AddressRecord[] | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const needle = query.trim();
    if (needle.length < 2) return;
    setWorking("search");
    setError("");
    try {
      setHits(
        await clientApi<AddressRecord[]>(
          `/address-lookup/addresses?q=${encodeURIComponent(needle)}&limit=8`,
        ),
      );
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "That search failed.");
    }
    setWorking(null);
  }

  async function save(address: AddressRecord) {
    setWorking(address.address_id);
    setError("");
    try {
      await clientApi<Place>("/accounts/me/places", {
        method: "POST",
        body: JSON.stringify({ address_id: address.address_id }),
      });
      onDone({
        tool_use_id: card.tool_use_id,
        kind: card.kind,
        value: { address_id: address.address_id },
      });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
      setWorking(null);
    }
  }

  return (
    <>
      <form onSubmit={search} className="chat-row">
        <input
          className="field"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Street or city"
          aria-label="Search the sample for a building"
          disabled={busy}
        />
        <button type="submit" className="btn" disabled={busy || working === "search"}>
          {working === "search" ? <Spinner /> : null}
          Search
        </button>
      </form>
      <Problem detail={error} />
      {hits !== null && hits.length === 0 && (
        <div className="chat-card-note">
          <p>
            Nothing in the sample matches that. The street and the city are
            matched separately, so the two together find nothing — and the
            sample is 500 addresses across a handful of cities, so a building
            that is not in it cannot be answered here.
          </p>
          {narrower(query).length > 0 && (
            <p className="mt-2">
              Try{" "}
              {narrower(query).map((term, index) => (
                <span key={term}>
                  {index > 0 && " or "}
                  <button
                    type="button"
                    className="chat-retry"
                    onClick={() => {
                      setQuery(term);
                      setHits(null);
                    }}
                  >
                    {term}
                  </button>
                </span>
              ))}
              .
            </p>
          )}
        </div>
      )}
      {hits !== null && hits.length > 0 && (
        <ul className="chat-options">
          {hits.map((hit) => (
            <li key={hit.address_id}>
              <button
                type="button"
                className="chat-option"
                onClick={() => save(hit)}
                disabled={busy || working !== null}
              >
                <span className="chat-option-main">{hit.street_address}</span>
                <span className="chat-option-sub">
                  {hit.postal_city}, {hit.state}
                  {" · "}
                  {hit.year_built ? `built ${hit.year_built}` : "year built not in the record"}
                  {" · "}
                  {hit.units ? `${hit.units} units` : "unit count not in the record"}
                </span>
                {working === hit.address_id && <Spinner />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/* ------------------------------------------------------- pick a building */

function PickBuilding({
  card,
  onDone,
  busy,
}: {
  card: PickBuildingCard;
  onDone: (resolution: Resolution) => void;
  busy: boolean;
}) {
  return (
    <ul className="chat-options">
      {card.options.map((option) => (
        <li key={option.place_id}>
          <button
            type="button"
            className="chat-option"
            disabled={busy}
            onClick={() =>
              onDone({
                tool_use_id: card.tool_use_id,
                kind: card.kind,
                value: { address_id: option.address_id },
              })
            }
          >
            <span className="chat-option-main">{option.label}</span>
            <span className="chat-option-sub">{option.where}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------- file a document */

/** Upload an agreement.
 *
 * The term and the rent are optional and self-reported. Nothing reads the
 * file, which is why they are typed here at all — and why the note under the
 * form says so rather than leaving somebody to assume otherwise.
 */
function UploadDocument({
  card,
  onDone,
  busy,
}: {
  card: DocumentCard;
  onDone: (resolution: Resolution) => void;
  busy: boolean;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const file = form.get("file");
    if (!(file instanceof File) || file.size === 0) {
      setError("Choose a file first.");
      return;
    }
    // The rent is typed in whole currency and stored in cents.
    const rent = String(form.get("rent") ?? "").trim();
    form.delete("rent");
    if (rent) {
      const cents = Math.round(Number(rent) * 100);
      if (Number.isFinite(cents) && cents >= 0) {
        form.set("monthly_rent_cents", String(cents));
      }
    }
    for (const [key, value] of [...form.entries()]) {
      if (typeof value === "string" && value.trim() === "") form.delete(key);
    }

    setWorking(true);
    setError("");
    try {
      const filed = await clientUpload<Contract>(
        `/accounts/me/places/${card.place_id}/contracts`,
        form,
      );
      onDone({
        tool_use_id: card.tool_use_id,
        kind: card.kind,
        value: { document_id: filed.id },
      });
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "The upload did not go through.",
      );
      setWorking(false);
    }
  }

  return (
    <form onSubmit={submit} className="chat-form">
      <p className="chat-card-note">
        Against <strong>{card.street}</strong> ({card.address_id}). Stored
        against your account and readable by nobody else.
      </p>
      <input
        type="file"
        name="file"
        className="field"
        accept=".pdf,.jpg,.jpeg,.png,.heic,.webp,.txt,.doc,.docx"
        disabled={busy || working}
        required
      />
      <div className="chat-grid">
        <label>
          <span>Unit, if the agreement covers one</span>
          <input className="field" name="unit_label" maxLength={64} disabled={busy || working} />
        </label>
        <label>
          <span>Rent a month</span>
          <input
            className="field"
            name="rent"
            type="number"
            min="0"
            step="0.01"
            inputMode="decimal"
            disabled={busy || working}
          />
        </label>
        <label>
          <span>Term starts</span>
          <input className="field" name="starts_on" type="date" disabled={busy || working} />
        </label>
        <label>
          <span>Term ends</span>
          <input className="field" name="ends_on" type="date" disabled={busy || working} />
        </label>
      </div>
      <p className="chat-card-note">
        Nothing reads the file. The term and the rent are yours, are never
        checked against it, and are never used in a calculation — they are shown
        beside the rule that governs them so you can read the two together.
      </p>
      <Problem detail={error} />
      <button type="submit" className="btn btn-primary" disabled={busy || working}>
        {working ? <Spinner /> : null}
        File it
      </button>
    </form>
  );
}

/* -------------------------------------------------- correct the details */

const LABELS: Record<string, string> = {
  unit_label: "Unit",
  starts_on: "Term starts",
  ends_on: "Term ends",
  monthly_rent: "Rent a month",
  note: "Note",
};

function AgreementDetails({
  card,
  onDone,
  busy,
}: {
  card: AgreementDetailsCard;
  onDone: (resolution: Resolution) => void;
  busy: boolean;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const read = (key: string) => {
      const value = String(form.get(key) ?? "").trim();
      return value === "" ? null : value;
    };
    const rent = read("monthly_rent");
    setWorking(true);
    setError("");
    try {
      // Every field in one request, because the API sets them all from the
      // body: sending one alone would clear the others.
      await clientApi<Contract>(`/accounts/me/contracts/${card.document_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          unit_label: read("unit_label") ?? card.current.unit_label,
          starts_on: read("starts_on") ?? card.current.starts_on,
          ends_on: read("ends_on") ?? card.current.ends_on,
          monthly_rent_cents:
            rent === null
              ? card.current.monthly_rent_cents
              : Math.round(Number(rent) * 100),
          note: read("note") ?? card.current.note,
        }),
      });
      onDone({
        tool_use_id: card.tool_use_id,
        kind: card.kind,
        value: { document_id: card.document_id },
      });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save that.");
      setWorking(false);
    }
  }

  return (
    <form onSubmit={submit} className="chat-form">
      <p className="chat-card-note">
        On <strong>{card.filename}</strong>. Self-reported, and never checked
        against the file.
      </p>
      <div className="chat-grid">
        {card.fields.map((field) => (
          <label key={field}>
            <span>{LABELS[field] ?? field}</span>
            <input
              className="field"
              name={field}
              type={
                field === "starts_on" || field === "ends_on"
                  ? "date"
                  : field === "monthly_rent"
                    ? "number"
                    : "text"
              }
              step={field === "monthly_rent" ? "0.01" : undefined}
              min={field === "monthly_rent" ? "0" : undefined}
              defaultValue={current(card, field)}
              disabled={busy || working}
            />
          </label>
        ))}
      </div>
      <Problem detail={error} />
      <button type="submit" className="btn btn-primary" disabled={busy || working}>
        {working ? <Spinner /> : null}
        Save
      </button>
    </form>
  );
}

function current(card: AgreementDetailsCard, field: string): string {
  if (field === "monthly_rent") {
    return card.current.monthly_rent_cents === null
      ? ""
      : String(card.current.monthly_rent_cents / 100);
  }
  const value = card.current[field as keyof typeof card.current];
  return value === null || value === undefined ? "" : String(value);
}

/* ------------------------------------------------------------ a page link */

function ViewLink({ card }: { card: ViewCard }) {
  return (
    <Link href={card.href} className="btn">
      {card.label} →
    </Link>
  );
}

/** The halves of a query that found nothing.
 *
 * Somebody typing "Taylor St San Francisco" has given the search both of the
 * fields it matches separately, which is the one failure that looks like the
 * building is missing when it is not. Splitting on the last capitalised run is
 * crude and sometimes wrong — so these are offered as something to try rather
 * than searched automatically.
 */
function narrower(query: string): string[] {
  const words = query.trim().split(/\s+/);
  if (words.length < 3) return [];
  // Two guesses: everything before the last two words, and the last two.
  const head = words.slice(0, -2).join(" ");
  const tail = words.slice(-2).join(" ");
  return [head, tail].filter((part) => part.length >= 2);
}

function Problem({ detail }: { detail: string }) {
  if (!detail) return null;
  return (
    <p role="alert" className="chat-card-error">
      {detail}
    </p>
  );
}
