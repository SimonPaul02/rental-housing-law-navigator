/** The four roles.
 *
 * Right now they are a recorded fact about a person and nothing more: every
 * role gets the same navigation, the same dashboard and the same pages. The
 * tailoring comes later, and `NAV` plus `components/home/dashboard.tsx` are the
 * two places it will branch — which is the reason the role is already asked for
 * and stored rather than retrofitted onto accounts that exist.
 *
 * The slugs match `Role` in backend/app/modules/accounts/schemas.py.
 *
 * They are not a hierarchy — a housing agency is not a renter with more
 * buttons — so nothing here orders or nests them. And none of them is a
 * permission: every row this app serves is either public corpus material or
 * the signed-in person's own, so a role decides what someone is *shown*, not
 * what they are *allowed*. That stays true when the tailoring arrives.
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
}

export const ROLES: Record<Role, RoleSpec> = {
  renter: {
    label: "Renter",
    tagline: "I rent a home and want to know what protects me.",
    blurb:
      "Which rules apply to the building you live in, in plain terms, each one quoting the law it comes from.",
  },
  provider: {
    label: "Housing provider",
    tagline: "I own or manage rental housing and have to comply.",
    blurb:
      "What each of your buildings is subject to, which exemptions it can claim, and which facts are still missing before an answer is possible.",
  },
  agency: {
    label: "Housing agency",
    tagline: "I work for a public body and oversee housing stock.",
    blurb:
      "Coverage across the whole sample: which addresses resolve to which legal jurisdiction, where the records are incomplete, and how many buildings each change case moves.",
  },
  advocate: {
    label: "Housing advocate",
    tagline: "I advise or represent tenants and need the sources.",
    blurb:
      "The rule record behind every answer — the quoted span, the citation, the source document — and the conflicts that still need a human.",
  },
};

/** One menu, for everybody.
 *
 * When the four apps diverge this becomes a function of the role; until then a
 * single list is the honest version, and nothing has to pretend to be tailored.
 */
export const NAV: NavItem[] = [
  { href: "/home", label: "Overview" },
  { href: "/places", label: "Your addresses" },
  { href: "/rules", label: "Rules" },
  { href: "/addresses", label: "Addresses" },
  { href: "/changes", label: "Changes" },
];

/** What a saved building is called. Shared for the same reason the menu is. */
export const PLACE = {
  one: "saved address",
  many: "saved addresses",
  add: "Your addresses",
};

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLE_SLUGS as readonly string[]).includes(value);
}
