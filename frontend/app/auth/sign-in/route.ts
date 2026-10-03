import { getWorkOS, saveSession } from "@workos-inc/authkit-nextjs";
import type { NextRequest } from "next/server";

/** Signing in without leaving the page.
 *
 * The server is what asks WorkOS, because only the server holds the API key,
 * and it seals the answer into exactly the same session cookie the hosted form
 * would have written. Everything downstream — the middleware, /auth/token, the
 * API's signature check — cannot tell the two apart, and that is the point:
 * the embedded form is a second door into the same room, not a second room.
 *
 * It covers email and password and nothing else, on purpose. A second factor,
 * an identity provider, an account with no password: the hosted form does all
 * of that, and reproducing it here would mean keeping a copy of its state
 * machine. What this route cannot finish, it says so and offers the hosted
 * way instead.
 */
const CLIENT_ID = process.env.WORKOS_CLIENT_ID ?? "";

const WRONG = "That email and password do not match an account.";
const ELSEWHERE = "This sign-in needs another step. Continue with WorkOS.";

function codeOf(error: unknown): string {
  const body = error as { code?: string; rawData?: { code?: string } };
  return body?.code || body?.rawData?.code || "";
}

export async function POST(request: NextRequest) {
  // Login CSRF: without this check another site could sign somebody into an
  // account it controls without them noticing. Two real headers are compared
  // rather than `request.url`, whose host Next rewrites to `localhost` in
  // development no matter which name the browser asked for — a check against
  // that turns our own page away.
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (origin && host && new URL(origin).host !== host) {
    return Response.json({ detail: "Origin not allowed." }, { status: 403 });
  }
  if (!CLIENT_ID) {
    return Response.json(
      { detail: "Sign-in is not configured in this environment." },
      { status: 503 },
    );
  }

  const { email, password } = await request
    .json()
    .catch(() => ({}) as Record<string, string>);
  if (!email || !password) {
    return Response.json(
      { detail: "Enter an email address and a password." },
      { status: 400 },
    );
  }

  try {
    const authenticated =
      await getWorkOS().userManagement.authenticateWithPassword({
        clientId: CLIENT_ID,
        email,
        password,
        // How WorkOS judges whether an attempt is unusual. Without these every
        // attempt would look like it came from the server itself, and the
        // judgement would be blind.
        ipAddress: request.headers.get("x-forwarded-for")?.split(",")[0].trim(),
        userAgent: request.headers.get("user-agent") ?? undefined,
      });
    // From here the session is indistinguishable from a hosted one.
    await saveSession(authenticated, request);
    return Response.json({ ok: true });
  } catch (error) {
    const reason = codeOf(error);
    // WorkOS answers "no such address" and "wrong password" identically on
    // purpose. That is not made finer here, or the form would tell a stranger
    // who has an account.
    if (reason === "invalid_credentials") {
      return Response.json({ detail: WRONG }, { status: 401 });
    }
    console.error("[sign-in]", reason || error);
    return Response.json({ detail: ELSEWHERE, hosted: true }, { status: 401 });
  }
}
