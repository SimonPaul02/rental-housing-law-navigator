import { gate, displayName } from "@/lib/auth";
import { RolePicker } from "@/components/role-picker";
import { Card, SectionTitle } from "@/components/ui";
import { SignInNotConfigured, Unavailable } from "@/components/gate-notice";

export const dynamic = "force-dynamic";

/** The account, and the one knob on it.
 *
 * Switching role is self-service because there is nobody to ask: with no
 * organisations there is no administrator above an account. It grants nothing
 * either - every row this app serves is either public corpus material or the
 * caller's own - so the only thing a switch changes is which of the four apps
 * you see. Saved addresses survive it and are simply read differently: the
 * building a renter calls home is a provider's first portfolio entry.
 */
export default async function SettingsPage() {
  const g = await gate();
  if (g.mode === "unavailable") return <Unavailable detail={g.detail} />;
  if (g.mode === "open") return <SignInNotConfigured what="An account page" />;

  const { account, user } = g;

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">Your account</h1>
        <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
          Signed in as {displayName(user)} ({account.email}). Your password,
          your email address and your Google connection are held by WorkOS, not
          here — change them there.
        </p>
      </section>

      <Card>
        <SectionTitle
          title="Your role"
          hint="Switching changes which of the four apps you get — the overview, the menu, and what your saved addresses are called. It is never a permission: what you may read does not depend on it, and nothing saved is lost."
        />
        <RolePicker current={account.role} onDone="/home" />
      </Card>
    </div>
  );
}
