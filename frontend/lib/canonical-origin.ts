/** The one origin an AuthKit flow may start on.
 *
 * Both `/sign-in` and `/sign-up` leave the one-time verifier cookie that the
 * callback checks the visitor against, and a cookie is readable only on the
 * host it was set on: `localhost` and `127.0.0.1` are the same machine but not
 * the same origin. AuthKit always returns to the single registered redirect
 * URI, so a flow begun on the other name comes back to a callback with no
 * cookie to verify and dies as a flat "couldn't sign in" — and `next dev`
 * prints `localhost` in its banner, which makes that the ordinary way in
 * rather than an unlucky one. So a flow starts on the host the redirect URI
 * names, or not at all.
 */
const redirectUri = process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI;
const canonical = redirectUri ? new URL(redirectUri) : null;

/** A 307 to the canonical host, or null when the request is already on it. */
export function canonicalHostRedirect(
  request: Request,
  pathname: string,
): Response | null {
  const host = request.headers.get("host");
  if (!canonical || !host || host === canonical.host) return null;
  return Response.redirect(
    new URL(pathname + new URL(request.url).search, canonical.origin),
    307,
  );
}
