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
 * caller's own - so the only thing a switch changes is which app you see.
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
        <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
          Signed in as {displayName(user)} ({account.email}). Your password,
          your email address and your Google connection are held by WorkOS, not
          here — change them there.
        </p>
      </section>

      <Card>
        <SectionTitle
          title="Your role"
          hint="Which of the four apps you get. It decides what you are shown, not what you are allowed."
        />
        <RolePicker current={account.role} onDone="/home" />
      </Card>
    </div>
  );
}
