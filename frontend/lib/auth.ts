/** Who is looking at this page, and may they.
 *
 * One function answers both, because the two questions have the same answer in
 * this app: a person's role *is* their app. `gate()` resolves the session,
 * sends anyone who cannot be here somewhere they can, and hands the page back
 * a role it can render against.
 *
 * It is not the security boundary. The API verifies the WorkOS token's
 * signature on every single request and is the only thing that decides who may
 * read what; this is what decides what a person is *shown*. Keeping those two
 * apart is deliberate — a redirect is a courtesy, not a lock.
 */

import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { withAuth } from "@workos-inc/authkit-nextjs";
import type { User } from "@workos-inc/node";
import { ApiError, api } from "./api";
import { isRole, type Role } from "./roles";
import type { Account } from "./types";

/** Whether this deployment has a WorkOS environment at all.
 *
 * Without one the app runs open, which is what a bare `make dev-web` does: the
 * API is open too (it makes the same call for itself), so the two never
 * disagree about whether anyone has to sign in. Production is configured or it
 * serves nobody — see the backend's lifespan check.
 */
export const signInConfigured = Boolean(process.env.WORKOS_CLIENT_ID);

export type Viewer =
  | { state: "open" }
  | { state: "anonymous" }
  | { state: "unregistered"; user: User }
  | { state: "ready"; user: User; account: Account; role: Role }
  | { state: "unavailable"; user: User | null; detail: string };

/** The raw session state, memoised for the length of one render.
 *
 * A page and its layout both want to know who is signed in; without this they
 * would ask the backend twice for the same answer.
 */
export const viewer = cache(async (): Promise<Viewer> => {
  if (!signInConfigured) return { state: "open" };

  let user: User | null = null;
  try {
    user = (await withAuth()).user ?? null;
  } catch {
    user = null;
  }
  if (!user) return { state: "anonymous" };

  // `null` here means signed in but no role chosen yet — an ordinary step in
  // signing up, which is why it is a 200 and not a 404. An actual failure must
  // not be mistaken for it: sending someone to the role picker because the
  // database is down would offer them a choice that cannot be saved.
  try {
    const account = await api<Account | null>("/api/accounts/me");
    if (!account) return { state: "unregistered", user };
    if (!isRole(account.role)) {
      return {
        state: "unavailable",
        user,
        detail: `The account carries an unknown role (${account.role}).`,
      };
    }
    return { state: "ready", user, account, role: account.role };
  } catch (error) {
    const detail =
      error instanceof ApiError && error.status === 503
        ? "The API has no WorkOS client id, so it cannot tell who is signed in."
        : "The API did not answer. It may still be starting up.";
    return { state: "unavailable", user, detail };
  }
});

export type Gate =
  | { mode: "open" }
  | { mode: "account"; user: User; account: Account; role: Role }
  | { mode: "unavailable"; detail: string };

/** Resolve the viewer into something a page can render, redirecting if not.
 *
 * Every role currently reaches every page, so there is nothing to check a path
 * against and this takes no argument. When the four apps diverge, the role on
 * the returned gate is what a page will branch on — it is already here, so no
 * page has to learn about roles twice.
 */
export async function gate(): Promise<Gate> {
  const seen = await viewer();

  switch (seen.state) {
    case "open":
      // No role exists in this mode, so there is nothing to gate on and every
      // page is reachable. Pages that are *about* a role say so themselves.
      return { mode: "open" };
    case "anonymous":
      redirect("/");
    // eslint-disable-next-line no-fallthrough -- redirect() never returns
    case "unregistered":
      redirect("/welcome");
    // eslint-disable-next-line no-fallthrough -- redirect() never returns
    case "unavailable":
      return { mode: "unavailable", detail: seen.detail };
    case "ready":
      return {
        mode: "account",
        user: seen.user,
        account: seen.account,
        role: seen.role,
      };
  }
}

/** The display name for a signed-in person, falling back to their address. */
export function displayName(user: User): string {
  const full = [user.firstName, user.lastName].filter(Boolean).join(" ").trim();
  return user.name || full || user.email;
}
