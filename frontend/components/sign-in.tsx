"use client";

import { useState, type FormEvent } from "react";
import { GoogleMark, Spinner } from "./google-mark";

/** The front door.
 *
 * Email and password are posted to /auth/sign-in, which asks WorkOS
 * server-side and writes the same session cookie the hosted form would — so
 * signing in does not navigate away from the app. Google necessarily goes to
 * Google for a moment: its consent screen lives on its own domain, as every
 * provider's does. WorkOS's intermediate screen is still skipped, because the
 * button names the provider.
 *
 * Creating an account with a password is the one thing that leaves: a new
 * address has to be verified, and the hosted form already is that flow. See
 * app/sign-up/route.ts.
 */
export function SignIn({ configured }: { configured: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [hosted, setHosted] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setHosted(false);
    try {
      const response = await fetch("/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        // The cookie is set. A full load rather than a client navigation,
        // because every server component on the next page has to read the new
        // session — and the role picker is decided on the server.
        window.location.assign("/home");
        return;
      }
      setError(body.detail || "Sign-in failed. Please try again.");
      setHosted(Boolean(body.hosted));
    } catch {
      setError("Could not reach the server. Please try again.");
    }
    setBusy(false);
  }

  if (!configured) {
    return (
      <div className="auth-shell space-y-3 text-sm">
        <p className="font-medium" style={{ color: "var(--ink)" }}>
          Sign-in is not configured here.
        </p>
        <p style={{ color: "var(--muted)" }}>
          Set <code className="mono">WORKOS_CLIENT_ID</code>,{" "}
          <code className="mono">WORKOS_API_KEY</code> and{" "}
          <code className="mono">WORKOS_COOKIE_PASSWORD</code> in{" "}
          <code className="mono">frontend/.env.local</code> to turn accounts on.
          Until then the corpus views below are open, which is how a bare
          checkout runs.
        </p>
      </div>
    );
  }

  return (
    <div className="auth-shell space-y-4">
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          <span style={{ color: "var(--muted)" }}>Email</span>
          <input
            className="field mt-1"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className="block text-sm">
          <span style={{ color: "var(--muted)" }}>Password</span>
          <input
            className="field mt-1"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p
            role="alert"
            className="text-sm"
            style={{ color: "var(--critical)" }}
          >
            {error}{" "}
            {hosted && (
              <a href="/sign-in" className="underline">
                Continue with WorkOS
              </a>
            )}
          </p>
        )}
        <button className="btn btn-primary w-full" disabled={busy}>
          Sign in {busy && <Spinner />}
        </button>
      </form>

      <div className="auth-rule">or</div>

      <a className="btn w-full" href="/sign-in?provider=google">
        <GoogleMark />
        Continue with Google
      </a>

      <p className="text-center text-sm" style={{ color: "var(--faint)" }}>
        New here?{" "}
        <a href="/sign-up" className="underline">
          Create an account
        </a>
      </p>
    </div>
  );
}
