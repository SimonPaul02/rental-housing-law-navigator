import Link from "next/link";
import { JurisdictionBadge, Notice, ZipDiscrepancyBadge } from "@/components/ui";
import type { Place, RuleOutcome } from "@/lib/types";

/** The pieces all four dashboards need, and nothing that only one does.
 *
 * What belongs here is a fact about an address that reads the same to
 * everybody — where the building legally is, how old it is. What does not is
 * any framing of it: a renter's "your home" and an agency's "record A0132"
 * are the same row and deliberately not the same component.
 */

export function BackendDown() {
  return (
    <Notice title="Backend unreachable" tone="warning">
      Nothing answered at <code className="mono">/api/health</code>. Start it with{" "}
      <code className="mono">make dev-api</code> locally, or check{" "}
      <code className="mono">fly status</code> for the deployed machine.
    </Notice>
  );
}

export function NoRules() {
  return (
    <Notice title="No rules have been read in yet" tone="warning">
      Module A has not run on this deployment, so there is nothing to measure an
      address against. Nothing is wrong with the address.{" "}
      <code className="mono">POST /api/rule-extraction/extract</code> starts a pass.
    </Notice>
  );
}

/** Where a building legally is — and whether that is the city on its post.
 *
 * The mailing city is not the legal city: a good many rows on file are Boston
 * neighbourhoods and one is San Diego. Which city's rules reach a building is
 * the single most consequential thing about it, so it is never a click away in
 * any of the four views.
 */
export function WhereItIs({ place }: { place: Place }) {
  if (place.jurisdiction_status !== "resolved") {
    return (
      <span>
        {place.postal_city}, {place.state}{" "}
        <span style={{ color: "var(--faint)" }}>
          — mailing city. The legal city has not been verified, so city rules
          cannot be settled yet.
        </span>
      </span>
    );
  }
  return (
    <span>
      {place.legal_city}, {place.legal_state ?? place.state}
      {place.postal_city_differs && (
        <span style={{ color: "var(--faint)" }}>
          {" "}
          — the post says <strong>{place.postal_city}</strong>, but the rules
          that reach this building are {place.legal_city}&rsquo;s.
        </span>
      )}
    </span>
  );
}

/** Year built and unit count, the two facts most coverage conditions turn on. */
export function Facts({ place }: { place: Place }) {
  return (
    <span className="tabular-nums" style={{ color: "var(--faint)" }}>
      {place.year_built ? `built ${place.year_built}` : "year built not in the record"}
      {" · "}
      {place.units ? `${place.units} units` : "unit count not in the record"}
    </span>
  );
}

export function PlaceBadges({ place }: { place: Place }) {
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      <JurisdictionBadge status={place.jurisdiction_status} />
      {place.zip_discrepancy && <ZipDiscrepancyBadge />}
    </div>
  );
}

/** The link to the saved-address page, worded by whoever is sending them. */
export function ManageLink({ href = "/places", label }: { href?: string; label: string }) {
  return (
    <Link href={href} className="text-sm hover:underline" style={{ color: "var(--accent)" }}>
      {label} →
    </Link>
  );
}

/** The quoted span and the citation, as a reader checks an answer rather than
 *  trusting it. Every record in this system was kept only because that span was
 *  found verbatim in its source document. */
export function Source({ outcome }: { outcome: RuleOutcome }) {
  if (!outcome.quoted_span && !outcome.citation) return null;
  return (
    <>
      {outcome.quoted_span && (
        <blockquote
          className="mt-3 border-l-2 pl-3 text-sm italic"
          style={{ borderColor: "var(--line)", color: "var(--faint)" }}
        >
          &ldquo;{outcome.quoted_span}&rdquo;
        </blockquote>
      )}
      {outcome.citation && (
        <div className="mt-2 text-xs" style={{ color: "var(--faint)" }}>
          {outcome.source_url ? (
            <a
              className="underline"
              href={outcome.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {outcome.citation}
            </a>
          ) : (
            outcome.citation
          )}
        </div>
      )}
    </>
  );
}
