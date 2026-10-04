import Link from "next/link";
import { redirect } from "next/navigation";
import { RoleGrid } from "@/components/role-grid";
import { SignIn } from "@/components/sign-in";
import { Card, Notice } from "@/components/ui";
import { signInConfigured, viewer } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import type { Health, Meta } from "@/lib/types";

export const dynamic = "force-dynamic";

/** `rent_increase_limits` → `Rent increase limits`.
 *
 * The categories are the API's own list rather than a second copy kept here,
 * so what the front door claims to answer cannot drift from what the corpus
 * actually carries. Only the casing is ours.
 */
function humanise(category: string): string {
  const words = category.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** `2026-10-01` → `1 October 2026`, in UTC.
 *
 * Parsed by hand rather than through `new Date(iso)`: a bare date string is
 * UTC midnight, which in a western timezone renders as the day before.
 */
function longDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return iso;
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** The front door.
 *
 * Signed in, it is not a page at all — it forwards to whichever of the four
 * apps the person's role names, because there is no shared workspace to land
 * in. Not signed in, it is the only thing they see, so it has one job: say
 * what the navigator answers, and offer the ways in.
 *
 * The four roles appear once, as the tiles that sign in as them. They used to
 * appear twice — a bulleted explanation and a separate grid of demo accounts —
 * which is two places for the same four facts to drift apart in.
 */
export default async function FrontDoor() {
  const seen = await viewer();
  if (seen.state === "ready") redirect("/home");
  if (seen.state === "unregistered") redirect("/welcome");

  // Both are public: `/health` names no row and `/meta` is the vocabulary the
  // filters are built from. A cold backend costs the scope strip and raises
  // the notice at the foot; it never costs the page or the sign-in card.
  const [health, meta] = await Promise.all([
    tryApi<Health>("/api/health", { anonymous: true }),
    tryApi<Meta>("/api/meta", { anonymous: true, revalidate: 300 }),
  ]);

  return (
    <div className="front">
      <section className="front-hero">
        <div>
          <p className="eyebrow">Rental Housing Law Navigator</p>
          <h1 className="front-title">
            Which housing rules apply <em>here, on this date</em>?
          </h1>
          <p className="front-lede">
            Rental housing law for one building on one day, with the span of
            source text quoted behind every answer.
          </p>

          {meta && (
            <div className="front-scope">
              <p className="front-scope-label">What it answers</p>
              <ul className="front-chips">
                {meta.categories.map((category) => (
                  <li key={category}>{humanise(category)}</li>
                ))}
              </ul>
              <p className="front-note">
                State and city law, as it stood on the day you ask about —
                today that is {longDate(meta.default_as_of)}. A rule not yet in
                force says so rather than being left out, and a fact the public
                record does not carry is named instead of guessed.
              </p>
            </div>
          )}
        </div>

        <Card className="front-auth">
          <h2>Sign in</h2>
          <p className="front-auth-lede">
            Accounts live in WorkOS. Google works for both signing up and
            signing in.
          </p>
          <SignIn configured={signInConfigured} />
        </Card>
      </section>

      <section>
        <div className="front-section-head">
          <h2>One record, four apps</h2>
          <p>
            The four roles are not four skins of one page. What separates them
            is how many addresses each one has — one home, a portfolio, the
            whole sample and none of their own, or a caseload that never adds
            up — which decides whether a page can be one building in depth, a
            set in aggregate, or neither.
            {signInConfigured &&
              " Pick one to sign in: they are ordinary accounts with nothing withheld."}
          </p>
        </div>

        <RoleGrid configured={signInConfigured} />

        <p className="front-fine">
          A role is never a permission. Every row this app serves is either
          public corpus material or your own, so a role decides what you are
          shown, not what you may read. There is no team, no sharing and no
          inbox either — nothing in the app can address another account.
        </p>
      </section>

      {(seen.state === "open" || !health) && (
        <section className="front-notices">
          {seen.state === "open" && (
            <Notice title="Running without accounts">
              No WorkOS environment is configured, so this checkout runs open:
              the API serves anyone and the corpus views are reachable without
              signing in.{" "}
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
              . Production refuses to serve in this state rather than publishing
              the data.
            </Notice>
          )}

          {!health && (
            <Notice title="Backend unreachable" tone="warning">
              Nothing answered at <code className="mono">/api/health</code>.
              Start it with <code className="mono">make dev-api</code> locally,
              or check <code className="mono">fly status</code> for the deployed
              machine. Signing in will work; the data behind it will not.
            </Notice>
          )}
        </section>
      )}
    </div>
  );
}
