import Link from "next/link";
import { redirect } from "next/navigation";
import { DemoAccounts } from "@/components/demo-accounts";
import { SignIn } from "@/components/sign-in";
import { Card, Notice } from "@/components/ui";
import { ROLES, ROLE_SLUGS, type Cardinality } from "@/lib/roles";
import { signInConfigured, viewer } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import type { Health } from "@/lib/types";

export const dynamic = "force-dynamic";

/** How many addresses each role has, in four words.
 *
 * It is the first thing worth knowing about the four apps, because it is what
 * makes them different apps rather than four skins: a page about one building
 * and a page about five hundred cannot be the same page.
 */
const CARDINALITY: Record<Cardinality, string> = {
  one: "One address.",
  portfolio: "A portfolio, answered together.",
  caseload: "Many, never added up.",
  stock: "The whole sample, none of their own.",
};

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
      <section className="grid gap-12 lg:grid-cols-[1.25fr_0.9fr] lg:items-start">
        <div>
          <p className="eyebrow">Rental Housing Law Navigator</p>
          <h1
            className="display"
            style={{ fontSize: 34, lineHeight: 1.18, fontWeight: 650 }}
          >
            Which housing rules apply here, on this date?
          </h1>
          <p
            className="mt-3 max-w-xl leading-relaxed"
            style={{ color: "var(--muted)" }}
          >
            Rental housing law for a specific building on a specific day, with
            the source text behind every answer. Sign in and say which of these
            you are: one record, four apps, because the four do not even hold
            the same number of addresses.
          </p>

          <ul className="mt-7 space-y-3.5">
            {ROLE_SLUGS.map((slug) => (
              <li key={slug} className="flex gap-3">
                <span
                  aria-hidden
                  className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: "var(--accent)" }}
                />
                <span>
                  <strong className="font-medium">{ROLES[slug].label}</strong>{" "}
                  <span style={{ color: "var(--muted)" }}>
                    — {ROLES[slug].blurb}
                  </span>{" "}
                  <span style={{ color: "var(--faint)" }}>
                    {CARDINALITY[ROLES[slug].cardinality]}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-7 text-sm" style={{ color: "var(--faint)" }}>
            A role is never a permission — every row this app serves is either
            public corpus material or your own — so it decides what you are
            shown, not what you may read. No teams, no sharing, no inboxes
            either: nobody here can see anybody else, and there is nothing in
            the app that addresses another account.
          </p>
        </div>

        <Card>
          <h2 className="text-lg font-semibold tracking-tight">Sign in</h2>
          <p className="mt-1 mb-5 text-sm" style={{ color: "var(--muted)" }}>
            Accounts live in WorkOS. Google works for both signing up and
            signing in.
          </p>
          <SignIn configured={signInConfigured} />
        </Card>
      </section>

      {signInConfigured && (
        <Card>
          <h2 className="text-lg font-semibold tracking-tight">
            Or sign in as one of the four roles
          </h2>
          <p className="mt-1 mb-5 max-w-2xl text-sm" style={{ color: "var(--muted)" }}>
            The four roles get four different apps, so seeing all of them means
            being four different people. These accounts exist so you can do that
            without being issued anything — pick one, or type its credentials
            into the card above. They are ordinary accounts with nothing
            withheld. Signing out brings you back here to try the next one.
          </p>
          <DemoAccounts />
        </Card>
      )}

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
