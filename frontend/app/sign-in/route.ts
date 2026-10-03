import { getSignInUrl } from "@workos-inc/authkit-nextjs";
import { redirect } from "next/navigation";
import { canonicalHostRedirect } from "@/lib/canonical-origin";

/** The way into AuthKit's own hosted flow, as a plain link a card can point at.
 *
 * It doubles as the application's Initiate Login URI in the WorkOS dashboard,
 * which is what lets WorkOS start a flow of its own — impersonating a user
 * from the dashboard, for instance.
 *
 * `?provider=google` skips the hosted screen and goes straight to Google. The
 * URL is still built by the SDK so that PKCE and state stay exactly the ones
 * the callback will check; the only thing swapped is who WorkOS calls.
 * `screen_hint` has to go: it names a screen of the hosted form, and WorkOS
 * rejects it alongside a real provider.
 */
export const GET = async (request: Request) => {
  const elsewhere = canonicalHostRedirect(request, "/sign-in");
  if (elsewhere) return elsewhere;

  const target = new URL(await getSignInUrl());
  if (new URL(request.url).searchParams.get("provider") === "google") {
    target.searchParams.set("provider", "GoogleOAuth");
    target.searchParams.delete("screen_hint");
  }
  redirect(target.toString());
};
