"use client";

import { useState } from "react";
import { ROLES, ROLE_SLUGS, type Role } from "@/lib/roles";
import { clientApi } from "@/lib/client-api";
import { Spinner } from "./google-mark";

/** Choosing which of the four apps you get.
 *
 * Asked once, after signing up, because nothing WorkOS knows about a person
 * answers it — with no organisations there is no membership to read a role
 * from, and a Google sign-up hands us a verified address and nothing else.
 *
 * It is a radio group, not four buttons: the roles are alternatives, and a
 * screen reader should hear them as a choice with one answer.
 */
export function RolePicker({
  current,
  onDone,
}: {
  current?: Role;
  onDone?: string;
}) {
  const [chosen, setChosen] = useState<Role | undefined>(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!chosen || chosen === current) return;
    setBusy(true);
    setError("");
    try {
      if (current) {
        // Already registered, so this is a switch: the API can take it
        // straight from the browser, since the only thing that changes is a
        // field on the caller's own row.
        await clientApi("/accounts/me", {
          method: "PATCH",
          body: JSON.stringify({ role: chosen }),
        });
      } else {
        // First time: the profile has to come from the sealed session, which
        // only the server can open. See app/auth/register/route.ts.
        const response = await fetch("/auth/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: chosen }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail || "Could not save your role.");
        }
      }
      // A full load, not a client navigation: the navigation, the dashboard
      // and the route guards are all server-rendered from the role.
      window.location.assign(onDone ?? "/home");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save your role.");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="Your role" className="grid gap-3 sm:grid-cols-2">
        {ROLE_SLUGS.map((slug) => {
          const spec = ROLES[slug];
          const selected = chosen === slug;
          return (
            <button
              key={slug}
              type="button"
              role="radio"
              aria-checked={selected}
              className="role-card"
              disabled={busy}
              onClick={() => setChosen(slug)}
            >
              <span className="flex items-baseline justify-between gap-2">
                <span className="font-medium">{spec.label}</span>
                {slug === current && (
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                    current
                  </span>
                )}
              </span>
              <span
                className="mt-1 block text-sm"
                style={{ color: "var(--text-secondary)" }}
              >
                {spec.tagline}
              </span>
              <span
                className="mt-2 block text-sm leading-relaxed"
                style={{ color: "var(--text-muted)" }}
              >
                {spec.blurb}
              </span>
            </button>
          );
        })}
      </div>

      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--status-critical)" }}>
          {error}
        </p>
      )}

      <div className="flex items-center gap-3">
        <button
          className="btn btn-primary"
          disabled={busy || !chosen || chosen === current}
          onClick={save}
        >
          {current ? "Switch role" : "Continue"} {busy && <Spinner />}
        </button>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          You can change this later in settings.
        </p>
      </div>
    </div>
  );
}
