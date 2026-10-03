import { Notice } from "./ui";

/** What a page shows when `gate()` could not establish who is asking.
 *
 * Deliberately not a sign-in prompt: the session may be perfectly good and the
 * API simply unreachable, and sending somebody through a sign-in that cannot
 * help them wastes their time and hides the real fault.
 */
export function Unavailable({ detail }: { detail: string }) {
  return (
    <Notice title="Your account could not be loaded" tone="warning">
      {detail} Nothing is wrong with your sign-in — try again in a moment, or
      check <code className="mono">fly status</code> for the API machine.
    </Notice>
  );
}

/** What a role-specific page shows in a checkout with no WorkOS environment.
 *
 * There are no accounts in that mode, so there is no role either, and a page
 * built around one has nothing to render. The corpus views still work, which
 * is what makes the mode worth having.
 */
export function SignInNotConfigured({ what }: { what: string }) {
  return (
    <Notice title="This view needs an account" tone="warning">
      {what} belongs to a signed-in person, and this checkout has no WorkOS
      environment — set <code className="mono">WORKOS_CLIENT_ID</code>,{" "}
      <code className="mono">WORKOS_API_KEY</code> and{" "}
      <code className="mono">WORKOS_COOKIE_PASSWORD</code> in{" "}
      <code className="mono">frontend/.env.local</code>, plus the same client id
      for the API. The rule, address and change views work without it.
    </Notice>
  );
}
