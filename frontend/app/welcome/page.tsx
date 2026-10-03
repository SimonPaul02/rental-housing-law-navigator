import { redirect } from "next/navigation";
import { RolePicker } from "@/components/role-picker";
import { Unavailable } from "@/components/gate-notice";
import { displayName, viewer } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** The one question sign-up cannot answer by itself.
 *
 * WorkOS has verified who this is; nothing it holds says what they are here
 * for, and with no organisations there is no membership to read a role from.
 * So it is asked once, plainly, before anything else is shown.
 */
export default async function WelcomePage() {
  const seen = await viewer();
  if (seen.state === "anonymous" || seen.state === "open") redirect("/");
  if (seen.state === "ready") redirect("/home");
  if (seen.state === "unavailable") return <Unavailable detail={seen.detail} />;

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome, {displayName(seen.user)}.
        </h1>
        <p
          className="mt-2 max-w-2xl leading-relaxed"
          style={{ color: "var(--text-secondary)" }}
        >
          One question before you start: which of these are you? It decides
          which app you get — the four read the same record and ask entirely
          different things of it.
        </p>
      </section>

      <RolePicker />
    </div>
  );
}
