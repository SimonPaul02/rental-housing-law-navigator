import Link from "next/link";
import { redirect } from "next/navigation";
import { RoleCards } from "@/components/role-cards";
import { Specimen } from "@/components/specimen";
import { Notice } from "@/components/ui";
import { signInConfigured, viewer } from "@/lib/auth";
import { tryApi } from "@/lib/api";
import type { Health, Meta } from "@/lib/types";

export const dynamic = "force-dynamic";

/** `2026-10-01` -> `1 October 2026`, in UTC.
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

function ArrowRight() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M3.2 8h9.1m0 0L8.9 4.6M12.3 8l-3.4 3.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** The front door.
 *
 * Signed in, it is not a page at all — it forwards to whichever of the four
 * apps the person's role names, because there is no shared workspace to land
 * in. Not signed in, it has one job: say what the navigator answers, show it
 * answering, and point at the door.
 *
 * It holds no sign-in form. That is /login, and the split is the point: a
 * returning person should not have to scroll past the pitch to get in, and the
 * pitch should not be laid out around a form.
 */
export default async function FrontDoor() {
  const seen = await viewer();
  if (seen.state === "ready") redirect("/home");
  if (seen.state === "unregistered") redirect("/welcome");

  // Both are public: `/health` names no row and `/meta` is the vocabulary the
  // filters are built from. A cold backend costs the scope line and raises the
  // notice at the foot; it never costs the page.
  const [health, meta] = await Promise.all([
    tryApi<Health>("/api/health", { anonymous: true }),
    tryApi<Meta>("/api/meta", { anonymous: true, revalidate: 300 }),
  ]);

  return (
    <div className="front">
      {/* Light that moves, behind everything. Decoration, so it is hidden from
          assistive technology and stops entirely under reduced motion. */}
      <div className="front-aurora" aria-hidden>
        <span />
        <span />
      </div>
      <div className="front-grain" aria-hidden />

      <section className="front-hero">
        <p className="front-badge rise rise-1">
          <span className="front-pulse" aria-hidden />
          Rental Housing Law Navigator
        </p>

        <h1 className="front-title rise rise-2">
          Which housing rules apply <em>here, on this date</em>?
        </h1>

        <p className="front-lede rise rise-3">
          One building, one day, and the source text behind every answer.
        </p>

        <div className="front-cta rise rise-4">
          <Link className="cta cta-primary" href="/login">
            Sign in <ArrowRight />
          </Link>
          <a className="cta cta-ghost" href="#apps">
            See the four apps
          </a>
        </div>

        {meta && (
          <p className="front-scope rise rise-4">
            {meta.categories.length} obligations · state and city law · answered
            as of {longDate(meta.default_as_of)}
          </p>
        )}
      </section>

      <section className="front-specimen rise rise-5">
        <Specimen />
      </section>

      <section className="front-apps rise rise-6" id="apps">
        <div className="front-section-head">
          <p className="front-eyebrow">One record, four apps</p>
          <h2>What separates them is how many addresses each has</h2>
          <p>
            Not four skins of one page — and that difference decides what a page
            can even be about.
          </p>
        </div>

        <RoleCards />

        <p className="front-fine">
          A role is never a permission: it decides what you are shown, never
          what you may read. No teams, no sharing, no inboxes.
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
