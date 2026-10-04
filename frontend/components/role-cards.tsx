import Link from "next/link";
import { ROLES, ROLE_SLUGS, type Cardinality } from "@/lib/roles";
import { RoleArt } from "./role-art";

/** The four apps, on the front door — shown, not signed into.
 *
 * The clickable version of these lives on /login, where signing in belongs.
 * Here they are the argument: four cards that each carry a plate of the view
 * behind them, so "the four roles get four different apps" is demonstrated
 * rather than asserted.
 */

/** How many addresses this role has, in three words. The long version is
 *  `Cardinality` in lib/roles.ts. */
const CARDINALITY: Record<Cardinality, string> = {
  one: "One address",
  portfolio: "A portfolio",
  caseload: "A caseload",
  stock: "The whole sample",
};

export function RoleCards() {
  return (
    <div className="role-grid">
      {ROLE_SLUGS.map((slug) => {
        const role = ROLES[slug];
        return (
          <Link key={slug} href="/login" className="role-tile">
            <RoleArt role={slug} />
            <span className="role-kind">{CARDINALITY[role.cardinality]}</span>
            <span className="role-name">{role.label}</span>
            <span className="role-line">{role.tagline}</span>
          </Link>
        );
      })}
    </div>
  );
}
