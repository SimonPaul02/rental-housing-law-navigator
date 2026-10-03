import { handleAuth } from "@workos-inc/authkit-nextjs";

/** Where AuthKit returns somebody after they have signed in.
 *
 * Must match the redirect URI registered in the WorkOS dashboard and
 * `NEXT_PUBLIC_WORKOS_REDIRECT_URI`.
 *
 * The session cookie is set on the host this route is served from, so the hop
 * afterwards has to land on that same host or the browser stops sending it — a
 * silent sign-out that looks exactly like a failed login. Left to itself the
 * SDK takes the host from the request, and `next dev` normalises that to
 * `localhost` while the app may be browsed at `127.0.0.1`, which drops the
 * fresh session on a neighbouring origin. The redirect URI is the canonical
 * origin, so the return trip is pinned to it.
 */
const redirectUri = process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI;

export const GET = handleAuth(
  redirectUri ? { baseURL: new URL(redirectUri).origin } : {},
);
