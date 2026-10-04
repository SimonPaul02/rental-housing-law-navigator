"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Spinner } from "./google-mark";

/** Who is signed in, behind one button.
 *
 * It used to be an email address, a role and a sign-out button laid out side
 * by side in the nav pill — three things competing with the four menu items
 * next to them, and the first thing to wrap on a narrow window. All three are
 * facts about the account rather than places to go, so they belong together
 * under the account, which is what people reach for anyway.
 *
 * Signing out stays a POST, so a link in an email cannot do it on somebody's
 * behalf — see app/sign-out/route.ts.
 */
export function AccountMenu({
  name,
  email,
  roleLabel,
}: {
  name: string;
  email: string;
  roleLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const holder = useRef<HTMLDivElement | null>(null);

  // Close on a click anywhere else and on Escape. A menu that can only be
  // dismissed by hitting its own button again is a menu people leave open.
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!holder.current?.contains(event.target as Node)) setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  return (
    <div className="account" ref={holder}>
      <button
        type="button"
        className={`account-button${open ? " open" : ""}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(!open)}
      >
        <span className="account-mark" aria-hidden>
          {initials(name, email)}
        </span>
        <span className="account-name">{name}</span>
        <svg width="10" height="7" viewBox="0 0 12 8" fill="none" aria-hidden>
          <path
            d="M1 1.75 6 6.25 11 1.75"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div className="account-menu" role="menu">
          <div className="account-head">
            <div className="account-head-name">{name}</div>
            <div className="account-head-mail">{email}</div>
            <div className="account-role">{roleLabel}</div>
          </div>

          <Link href="/settings" className="account-item" role="menuitem" onClick={() => setOpen(false)}>
            Your account
            <span className="account-hint">change role, see what it decides</span>
          </Link>

          <form action="/sign-out" method="post" onSubmit={() => setBusy(true)}>
            <button type="submit" className="account-item account-signout" role="menuitem" disabled={busy}>
              Sign out {busy && <Spinner size={13} />}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

/** One or two letters for the mark. Falls back to the address, because a
 *  Google sign-up hands us a verified email and often nothing else. */
function initials(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  const source = words[0] ?? email;
  return source.slice(0, 2).toUpperCase();
}
