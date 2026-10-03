import { withAuth } from "@workos-inc/authkit-nextjs";
import type { NextRequest } from "next/server";
import { ApiError, api } from "@/lib/api";
import { isRole } from "@/lib/roles";
import type { Account } from "@/lib/types";

/** Finishing sign-up: the role, and the profile that goes with it.
 *
 * Why this route exists rather than the browser calling the API directly: the
 * API knows only what a verified token tells it, and an AuthKit access token
 * carries the user id and nothing else — no address, no name. Those live in
 * the sealed session, which only the server can open. So the server reads them
 * out of `withAuth()` and sends them on; the browser contributes the one thing
 * it is entitled to decide, which is the role.
 *
 * Even if a body arrived with somebody else's address in it, the API stores it
 * under the token's `sub`, so the worst that could happen is mislabelling your
 * own row. The reason to build the profile here anyway is simpler: it is the
 * real one.
 *
 * Idempotent, and called on every sign-in — a name or avatar changed in WorkOS
 * follows along without anyone having to notice.
 */
export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (origin && host && new URL(origin).host !== host) {
    return Response.json({ detail: "Origin not allowed." }, { status: 403 });
  }

  const { user } = await withAuth().catch(() => ({ user: null }));
  if (!user) {
    return Response.json({ detail: "Please sign in first." }, { status: 401 });
  }

  const { role } = await request.json().catch(() => ({}) as { role?: string });
  if (!isRole(role)) {
    return Response.json({ detail: "Choose one of the four roles." }, { status: 400 });
  }

  try {
    const account = await api<Account>("/api/accounts/me", {
      method: "PUT",
      body: JSON.stringify({
        role,
        email: user.email,
        name:
          user.name ||
          [user.firstName, user.lastName].filter(Boolean).join(" ") ||
          null,
        picture_url: user.profilePictureUrl ?? null,
      }),
    });
    return Response.json(account);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 502;
    console.error("[register]", error);
    return Response.json(
      {
        detail:
          status === 503
            ? "The API cannot check sign-in in this environment."
            : "Could not save your role. Please try again.",
      },
      { status },
    );
  }
}
