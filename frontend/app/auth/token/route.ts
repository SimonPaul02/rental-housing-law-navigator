import { withAuth } from "@workos-inc/authkit-nextjs";

/** Hands the browser a bearer token for the life of one request.
 *
 * The page holds a sealed cookie it cannot read; the API wants the access
 * token inside it. This is how a client component gets one, so nothing but the
 * session itself is ever stored in the browser. Not signed in — or no WorkOS
 * environment at all — is an answer rather than an error: the caller attaches
 * no header and lets the API decide.
 */
export async function GET() {
  const token = await withAuth()
    .then((auth) => auth.accessToken ?? null)
    .catch(() => null);
  return Response.json(
    { accessToken: token },
    { headers: { "Cache-Control": "no-store" } },
  );
}
