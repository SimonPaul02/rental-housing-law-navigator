"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** The filled Sign in button in the bar.
 *
 * Hidden on /login itself, where it would point at the page you are already
 * looking at. A client component only so it can read the path — the shell
 * around it stays a server component.
 */
export function SignInCta() {
  if (usePathname() === "/login") return null;
  return (
    <Link className="nav-item nav-cta" href="/login">
      Sign in
    </Link>
  );
}
