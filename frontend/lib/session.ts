/** The access token, for client components only.
 *
 * The AuthKit session is a cookie the page cannot read, so the bearer token the
 * API expects is fetched from the server and kept in memory only — never in
 * `localStorage`, where it would outlive the session and be readable by
 * anything else on the origin.
 *
 * It is reused until shortly before it expires; asking again after that
 * returns a fresh one, because the middleware refreshes the cookie on this very
 * path. Concurrent callers share one request, since a page settling can fire
 * several queries at once.
 */
const MARGIN_MS = 60_000;

let cached: { token: string; until: number } | null = null;
let pending: Promise<string | null> | null = null;

function expiry(token: string): number {
  try {
    const claims = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const { exp } = JSON.parse(atob(claims));
    return typeof exp === "number" ? exp * 1000 : 0;
  } catch {
    // An unreadable token still works as a bearer credential — the API is what
    // judges it. Not knowing when it dies only means not caching it.
    return 0;
  }
}

export async function accessToken(): Promise<string | null> {
  if (cached && Date.now() < cached.until) return cached.token;
  pending ??= fetch("/auth/token", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : { accessToken: null }))
    .then(({ accessToken }: { accessToken: string | null }) => {
      const until = accessToken ? expiry(accessToken) - MARGIN_MS : 0;
      cached = accessToken && until > Date.now() ? { token: accessToken, until } : null;
      return accessToken;
    })
    .catch(() => null)
    .finally(() => {
      pending = null;
    });
  return pending;
}

export function forgetAccessToken() {
  cached = null;
}
