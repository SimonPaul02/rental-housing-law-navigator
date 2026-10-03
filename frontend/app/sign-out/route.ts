import { signOut } from "@workos-inc/authkit-nextjs";

/** Ends the session and clears the cookie, then lands back on the front door.
 *
 * POST, not GET: a link somebody else controls — an image in an email, say —
 * must not be able to sign a person out. The header check is the same one
 * /auth/sign-in applies to signing in.
 */
export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (origin && host && new URL(origin).host !== host) {
    return Response.json({ detail: "Origin not allowed." }, { status: 403 });
  }
  await signOut({ returnTo: "/" });
}
