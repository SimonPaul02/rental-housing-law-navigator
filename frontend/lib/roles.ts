/** The four roles, and what each one's app is.
 *
 * One table, read by the role picker, the navigation, the dashboards and the
 * route guards, so a role can never be offered in one place and unknown in
 * another. The slugs match `Role` in backend/app/modules/accounts/schemas.py.
 *
 * They are not a hierarchy — a housing agency is not a renter with more
 * buttons — so nothing here orders or nests them. And none of them is a
 * permission: every row this app serves is either public corpus material or
 * the signed-in person's own, so the role decides what someone is *shown*, not
 * what they are *allowed*. Saying that plainly is the point; a role that
 * looked like a permission would invite treating it as one.
 */

export const ROLE_SLUGS = ["renter", "provider", "agency", "advocate"] as const;

export type Role = (typeof ROLE_SLUGS)[number];

export type NavItem = { href: string; label: string };

export interface RoleSpec {
  label: string;
  /** One line, on the button in the role picker. */
  tagline: string;
  /** The question this person brings to the data. */
  blurb: string;
  /** Heading of their dashboard. */
  home: string;
  nav: NavItem[];
  /** What a saved building is called to them.
   *
   * All four roles can keep a list - the page is the same one, and every entry
   * here is reachable from that role's own menu. What differs is only the noun:
   * a renter has a home, a provider has properties, an agency has a watchlist,
   * an advocate has case addresses. */
  place: { one: string; many: string; add: string };
}

export const ROLES: Record<Role, RoleSpec> = {
  renter: {
    label: "Renter",
    tagline: "I rent a home and want to know what protects me.",
    blurb:
      "Which rules apply to the building you live in, in plain terms, each one quoting the law it comes from.",
    home: "Your home",
    nav: [
      { href: "/home", label: "Your home" },
      { href: "/places", label: "Address" },
    ],
    place: { one: "home", many: "home", add: "Set your address" },
  },
  provider: {
    label: "Housing provider",
    tagline: "I own or manage rental housing and have to comply.",
    blurb:
      "What each of your buildings is subject to, which exemptions it can claim, and which facts are still missing before an answer is possible.",
    home: "Your properties",
    nav: [
      { href: "/home", label: "Your properties" },
      { href: "/places", label: "Properties" },
      { href: "/rules", label: "Obligations" },
    ],
    place: { one: "property", many: "properties", add: "Add a property" },
  },
  agency: {
    label: "Housing agency",
    tagline: "I work for a public body and oversee housing stock.",
    blurb:
      "Coverage across the whole sample: which addresses resolve to which legal jurisdiction, where the records are incomplete, and how many buildings each change case moves.",
    home: "Jurisdiction coverage",
    nav: [
      { href: "/home", label: "Coverage" },
      { href: "/rules", label: "Rule inventory" },
      { href: "/addresses", label: "Housing stock" },
      { href: "/changes", label: "Change cases" },
      { href: "/places", label: "Watchlist" },
    ],
    place: { one: "watched building", many: "watched buildings", add: "Watch a building" },
  },
  advocate: {
    label: "Housing advocate",
    tagline: "I advise or represent tenants and need the sources.",
    blurb:
      "The rule record behind every answer — the quoted span, the citation, the source document — and the conflicts that still need a human.",
    home: "Research",
    nav: [
      { href: "/home", label: "Research" },
      { href: "/rules", label: "Rule library" },
      { href: "/changes", label: "Change cases" },
      { href: "/addresses", label: "Housing stock" },
      { href: "/places", label: "Case addresses" },
    ],
    place: { one: "case address", many: "case addresses", add: "Add a case address" },
  },
};

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLE_SLUGS as readonly string[]).includes(value);
}

/** Which roles may open a given path. Derived from the navigation above, so a
 *  page can never be reachable by a role whose menu does not offer it. */
export function rolesFor(href: string): Role[] {
  return ROLE_SLUGS.filter((slug) =>
    ROLES[slug].nav.some((item) => item.href === href),
  );
}
