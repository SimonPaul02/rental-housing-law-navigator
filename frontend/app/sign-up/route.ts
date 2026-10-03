import { getSignUpUrl } from "@workos-inc/authkit-nextjs";
import { redirect } from "next/navigation";
import { canonicalHostRedirect } from "@/lib/canonical-origin";

/** Creating an account.
 *
 * Signing *in* happens in the page — see app/auth/sign-in/route.ts. Signing
 * *up* with a password does not, deliberately: a new address has to be
 * verified, which means a code, a resend, an expiry and a second screen. The
 * hosted form already is that state machine, correct and maintained, and
 * rebuilding it here would be keeping a worse copy of it.
 *
 * `?provider=google` goes straight to Google, where sign-up and sign-in are
 * the same act: the account arrives verified and the person lands on the role
 * picker without ever typing a password.
 */
export const GET = async (request: Request) => {
  const elsewhere = canonicalHostRedirect(request, "/sign-up");
  if (elsewhere) return elsewhere;

  const target = new URL(await getSignUpUrl());
  if (new URL(request.url).searchParams.get("provider") === "google") {
    target.searchParams.set("provider", "GoogleOAuth");
    target.searchParams.delete("screen_hint");
  }
  redirect(target.toString());
};
