"use client";

import { Spinner } from "./google-mark";
import { useState } from "react";

/** The signed-in person, and the way out.
 *
 * Signing out is a POST, so a link in an email cannot do it on someone's
 * behalf — see app/sign-out/route.ts.
 */
export function AccountMenu({
  name,
  roleLabel,
}: {
  name: string;
  roleLabel: string;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <form
      action="/sign-out"
      method="post"
      className="flex items-center gap-3 text-sm"
      onSubmit={() => setBusy(true)}
    >
      <span className="hidden sm:inline" style={{ color: "var(--text-muted)" }}>
        {name} · {roleLabel}
      </span>
      <button className="btn btn-quiet" disabled={busy}>
        Sign out {busy && <Spinner size={13} />}
      </button>
    </form>
  );
}
