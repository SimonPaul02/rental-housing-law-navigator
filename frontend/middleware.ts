import { authkitProxy } from "@workos-inc/authkit-nextjs";
import { NextResponse } from "next/server";

/** Session upkeep, not the access check.
 *
 * All this does is keep the sealed AuthKit cookie fresh on the paths that read
 * it, so a server component never gets a token that expires mid-render. What
 * actually stands between a stranger and the data is the API: it verifies the
 * token's signature on every request, and it is the only thing that does.
 *
 * A checkout with no WorkOS environment has no session to manage and is waved
 * through — which is how a bare `make dev-web` runs. The API makes the same
 * call for itself, so the two never disagree about whether anyone must sign in.
 */
export default process.env.WORKOS_CLIENT_ID
  ? authkitProxy()
  : () => NextResponse.next();

export const config = {
  /** Everything that reads a session, and nothing that makes one.
   *
   * Excluded, and each for its own reason:
   *
   * - `api/*` is a rewrite to FastAPI, which authenticates from the
   *   Authorization header and has no use for a cookie. Refreshing a session
   *   it will not read would only add a hop to every proxied call.
   * - `_next/*` and image files are assets; there is no session in a logo.
   * - `/callback`, `/sign-in`, `/sign-up`, `/sign-out` and `/auth/sign-in` are
   *   where a session is *created or destroyed*, not read. On an unauthenticated
   *   request this proxy mints a fresh PKCE verifier for a hypothetical sign-in,
   *   and `/callback` is precisely the request that has to find the *earlier*
   *   one still intact. Current AuthKit names each flow's verifier after its own
   *   state and strips the speculative cookie when the response is not a
   *   redirect to AuthKit, so this is safety rather than a live bug — but it is
   *   the kind of bug that comes back on an upgrade, and these routes gain
   *   nothing from being here.
   *
   * `/auth/token` stays in: handing out a bearer token is exactly when a
   * nearly-expired session must be refreshed first.
   */
  matcher: [
    "/((?!_next/|api/|(?:callback|sign-in|sign-up|sign-out)(?:/|$)|auth/sign-in(?:/|$)|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
