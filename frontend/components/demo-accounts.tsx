"use client";

import { useState } from "react";
import { DEMO_ACCOUNTS, demoLabel, type DemoAccount } from "@/lib/demo";
import { ROLES } from "@/lib/roles";
import { Spinner } from "./google-mark";

/** One button per role, so all four can be seen without being issued anything.
 *
 * Each button posts the same body to the same `/auth/sign-in` route the card
 * above uses, rather than taking a shortcut: a demo session should be the same
 * kind of session as a real one, or the demo stops proving anything. The reload
 * is a full navigation for the same reason it is in `SignIn` - the role is
 * resolved on the server for the next page.
 *
 * Only one of the four can be signed in at a time, since they are four separate
 * people; signing out returns here.
 */
export function DemoAccounts() {
  const [busy, setBusy] = useState<string>("");
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
      setError(body.detail || `Could not sign in as ${demoLabel(account)}.`);
    } catch {
      setError("Could not reach the server. Please try again.");
    }
    setBusy("");
  }

  return (
    <div className="space-y-3">
      <div className="demo-grid">
        {DEMO_ACCOUNTS.map((account) => (
          <button
            key={account.role}
            type="button"
            className="demo-card"
            onClick={() => signInAs(account)}
            disabled={busy !== ""}
          >
            <span className="demo-role">
              {demoLabel(account)}
              {busy === account.role && <Spinner />}
            </span>
            <span className="demo-tagline">{ROLES[account.role].tagline}</span>
            <span className="mono demo-cred">{account.email}</span>
            <span className="mono demo-cred">{account.password}</span>
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="text-sm" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
