"use client";

import { useState, type PointerEvent } from "react";
import { DEMO_ACCOUNTS, type DemoAccount } from "@/lib/demo";
import { ROLES, ROLE_SLUGS, type Cardinality, type Role } from "@/lib/roles";
import { RoleArt } from "./role-art";
import { Spinner } from "./google-mark";

/** The four apps, and the four ways in — one grid, because they are the same
 *  four things.
 *
 * The front door used to say the roles twice: a bulleted list explaining what
 * each one gets, and a separate grid of demo accounts to sign in with. They
 * were the same four facts in two places, which is how one of them goes stale.
 * A tile here names the role, says how many addresses it has, and *is* the
 * button that signs in as it.
 *
 * Each tile posts the same body to the same `/auth/sign-in` route the card
 * above uses, rather than taking a shortcut: a demo session should be the same
 * kind of session as a real one, or the demo stops proving anything. The reload
 * is a full navigation because the role is resolved on the server for the next
 * page.
 *
 * Only one of the four can be signed in at a time, since they are four separate
 * people; signing out returns here.
 *
 * Without a WorkOS environment there are no accounts to offer, so the same
 * tiles render as plain cards: the four apps are still what this page is about,
 * and nothing about them depends on being able to sign in.
 */

/** How many addresses this role has, in three words.
 *
 * It is the first thing worth knowing about the four, because it is what makes
 * them different apps rather than four skins: a page about one building and a
 * page about five hundred cannot be the same page. The long version lives in
 * `Cardinality` in lib/roles.ts.
 */
const CARDINALITY: Record<Cardinality, string> = {
  one: "One address",
  portfolio: "A portfolio",
  caseload: "A caseload",
  stock: "The whole stock on file",
};

/** Light that follows the cursor across a tile.
 *
 * Two custom properties and a radial gradient — no state, so moving the
 * pointer never re-renders React. A tile that has never been pointed at falls
 * back to a highlight at its own top edge, which is where the light would be
 * coming from anyway.
 */
function track(event: PointerEvent<HTMLElement>) {
  const tile = event.currentTarget;
  const box = tile.getBoundingClientRect();
  tile.style.setProperty("--mx", `${event.clientX - box.left}px`);
  tile.style.setProperty("--my", `${event.clientY - box.top}px`);
}

function Arrow() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M3.2 8h9.1m0 0L8.9 4.6M12.3 8l-3.4 3.4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function RoleGrid({ configured }: { configured: boolean }) {
  const [busy, setBusy] = useState<Role | "">("");
  const [error, setError] = useState("");

  async function signInAs(account: DemoAccount) {
    setBusy(account.role);
    setError("");
    try {
      const response = await fetch("/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: account.email, password: account.password }),
      });
      if (response.ok) {
        window.location.assign("/home");
        return;
      }
      const body = await response.json().catch(() => ({}));
      setError(
        body.detail || `Could not sign in as ${ROLES[account.role].label}.`,
      );
    } catch {
      setError("Could not reach the server. Please try again.");
    }
    setBusy("");
  }

  return (
    <div className="space-y-3">
      <div className="role-grid">
        {ROLE_SLUGS.map((slug) => {
          const role = ROLES[slug];
          const account = configured
            ? DEMO_ACCOUNTS.find((candidate) => candidate.role === slug)
            : undefined;

          const face = (
            <>
              <RoleArt role={slug} />
              <span className="role-kind">{CARDINALITY[role.cardinality]}</span>
              <span className="role-name">{role.label}</span>
              <span className="role-line">{role.tagline}</span>
              {account && (
                <span className="role-foot">
                  <span className="role-creds">
                    <span className="mono">{account.email}</span>
                    <span className="mono">{account.password}</span>
                  </span>
                  <span className="role-go">
                    {busy === slug ? <Spinner size={13} /> : <Arrow />}
                  </span>
                </span>
              )}
            </>
          );

          return account ? (
            <button
              key={slug}
              type="button"
              className={`role-tile${busy === slug ? " busy" : ""}`}
              aria-label={`Sign in as the ${role.label.toLowerCase()} demo account`}
              onPointerMove={track}
              onClick={() => signInAs(account)}
              disabled={busy !== ""}
            >
              {face}
            </button>
          ) : (
            <div key={slug} className="role-tile">
              {face}
            </div>
          );
        })}
      </div>
      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
