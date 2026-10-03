import Link from "next/link";
import { redirect } from "next/navigation";
import { SignIn } from "@/components/sign-in";
import { Card, Notice } from "@/components/ui";
import { ROLES, ROLE_SLUGS } from "@/lib/roles";
import { signInConfigured, viewer } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import type { Health } from "@/lib/types";

export const dynamic = "force-dynamic";

/** The front door.
 *
 * Signed in, it is not a page at all - it forwards to whichever of the four
 * apps the person's role names, because there is no shared workspace to land
 * in. Not signed in, it is the only thing they see.
 */
export default async function FrontDoor() {
  const seen = await viewer();
  if (seen.state === "ready") redirect("/home");
  if (seen.state === "unregistered") redirect("/welcome");

  const health = await tryApi<Health>("/api/health", { anonymous: true });

  return (
    <div className="space-y-10">
      <section className="grid gap-10 lg:grid-cols-[1.1fr_1fr] lg:items-start">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">
            Which housing rules apply here, on this date?
          </h1>
          <p
            className="mt-3 max-w-xl leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            Rental housing law for a specific building on a specific day, with
            the source text behind every answer. Sign in and say which of these
            you are — the same record, four different questions of it.
          </p>

          <ul className="mt-6 space-y-3">
            {ROLE_SLUGS.map((slug) => (
              <li key={slug} className="flex gap-3">
                <span
                  aria-hidden
                  className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: "var(--accent)" }}
                />
                <span>
                  <strong className="font-medium">{ROLES[slug].label}</strong>{" "}
                  <span style={{ color: "var(--text-secondary)" }}>
                    — {ROLES[slug].blurb}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-6 text-sm" style={{ color: "var(--text-muted)" }}>
            Every role shares one interface for now; the tailored views come
            later. No teams, no sharing, no inboxes either — nobody here can see
            anybody else, and there is nothing in the app that addresses another
            account.
          </p>
        </div>

        <Card>
          <h2 className="text-lg font-semibold tracking-tight">Sign in</h2>
          <p className="mt-1 mb-5 text-sm" style={{ color: "var(--text-secondary)" }}>
            Accounts live in WorkOS. Google works for both signing up and
            signing in.
          </p>
          <SignIn configured={signInConfigured} />
        </Card>
      </section>

      {seen.state === "open" && (
        <Notice title="Running without accounts">
          No WorkOS environment is configured, so this checkout runs open: the
          API serves anyone and the corpus views are reachable without signing
          in.{" "}
          <Link href="/rules" className="underline" style={{ color: "var(--accent)" }}>
            Rules
          </Link>
          {", "}
          <Link href="/addresses" className="underline" style={{ color: "var(--accent)" }}>
            addresses
          </Link>
          {", "}
          <Link href="/changes" className="underline" style={{ color: "var(--accent)" }}>
            changes
          </Link>
          . Production refuses to serve in this state rather than publishing the
          data.
        </Notice>
      )}

      {!health && (
        <Notice title="Backend unreachable" tone="warning">
          Nothing answered at <code className="mono">/api/health</code>. Start
          it with <code className="mono">make dev-api</code> locally, or check{" "}
          <code className="mono">fly status</code> for the deployed machine.
          Signing in will work; the data behind it will not.
        </Notice>
      )}
    </div>
  );
}
