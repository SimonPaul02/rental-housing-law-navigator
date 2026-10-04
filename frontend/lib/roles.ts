/** The four roles, and the four apps they get.
 *
 * The role is not decoration and not a permission. Every row this app serves
 * is either public corpus material or the signed-in person's own, so a role
 * decides what somebody is *shown* — which question the dashboard puts to the
 * same record, and what the saved-address list is even for.
 *
 * The slugs match `Role` in backend/app/modules/accounts/schemas.py.
 *
 * They are not a hierarchy — a housing agency is not a renter with more
 * buttons — so nothing here orders or nests them.
 */

export const ROLE_SLUGS = ["renter", "provider", "agency", "advocate"] as const;

export type Role = (typeof ROLE_SLUGS)[number];

export type NavItem = { href: string; label: string };

/** How many addresses this person actually has.
 *
 * This is the single most load-bearing difference between the four views, and
 * it is a fact about the people rather than a preference about layout:
 *
 * - `one`      A renter lives in one home. A second saved address is the old
 *              flat or one they are considering, never a set to aggregate — so
 *              the renter view is one building rendered deeply, and anything
 *              else is a footnote.
 * - `portfolio`A housing provider has many buildings and is answerable for all
 *              of them at once. The unit of work is the roll-up: what binds
 *              the portfolio, and which building is missing which fact. One
 *              building is the drill-down, not the page.
 * - `caseload` An advocate also has many, but they do not add up — each is a
 *              different person's situation, so a count across them would mean
 *              nothing. Each case stands alone, with its evidence attached.
 * - `stock`    An agency has none. Their subject is the whole sample, every
 *              address in it, whether or not anybody saved it; a saved address
 *              is at most a spot check they want to keep an eye on.
 */
export type Cardinality = "one" | "portfolio" | "caseload" | "stock";

export interface RoleSpec {
  label: string;
  /** One line, on the button in the role picker. */
  tagline: string;
  /** The question this person brings to the data. */
  blurb: string;
  cardinality: Cardinality;
  /** Why they have that many addresses, in one sentence, shown in the app. */
  cardinalityNote: string;
}

export const ROLES: Record<Role, RoleSpec> = {
  renter: {
    label: "Renter",
    tagline: "I rent a home and want to know what protects me.",
    blurb:
      "Which rules apply to the building you live in, in plain terms, each one quoting the law it comes from.",
    cardinality: "one",
    cardinalityNote:
      "You live in one home, so this app is about one building. Save another and it is kept as somewhere you are also watching — a flat you are considering, or the one you just left.",
  },
  provider: {
    label: "Housing provider",
    tagline: "I own or manage rental housing and have to comply.",
    blurb:
      "What each of your buildings is subject to, which exemptions it can claim, and which facts are still missing before an answer is possible.",
    cardinality: "portfolio",
    cardinalityNote:
      "You are answerable for every building at once, so the portfolio is the page and a single building is the drill-down.",
  },
  agency: {
    label: "Housing agency",
    tagline: "I work for a public body and oversee housing stock.",
    blurb:
      "Coverage across the whole sample: which addresses resolve to which legal jurisdiction, where the records are incomplete, and how many buildings each change case moves.",
    cardinality: "stock",
    cardinalityNote:
      "Your subject is the whole sample rather than any address of your own, so nothing here waits for you to save one. Spot checks are a convenience on top.",
  },
  advocate: {
    label: "Housing advocate",
    tagline: "I advise or represent tenants and need the sources.",
    blurb:
      "The rule record behind every answer — the quoted span, the citation, the source document — and the conflicts that still need a human.",
    cardinality: "caseload",
    cardinalityNote:
      "Each address is a different person's situation, so they are listed as cases and never added together. A count across them would mean nothing.",
  },
};

/** What a saved address is called, and what the list of them is for.
 *
 * The noun is not a synonym exercise: a renter's home, a provider's building,
 * an advocate's case and an agency's spot check are four different kinds of
 * thing stored in one table, and the words are what say so.
 */
export interface PlaceVocabulary {
  /** The page title, and the nav label. */
  title: string;
  one: string;
  many: string;
  /** Under the page title. */
  lede: string;
  /** Shown where the list would be, when it is empty. */
  empty: string;
  /** Above the search box. */
  search: string;
  /** What the optional name on a saved address is for, or null for no name. */
  label: string | null;
  /** What the optional note is for, or null for no note field. */
  note: string | null;
}

export const PLACES: Record<Role, PlaceVocabulary> = {
  renter: {
    title: "Your home",
    one: "home",
    many: "addresses",
    lede:
      "The building you live in. Private to you — nothing in this app can address another account, so there is nobody to share it with and nobody who can see it.",
    empty:
      "Find the building you live in and this app fills in: which rules cover it today, what each one entitles you to, and which answers are still blocked by a fact the public record does not carry.",
    search: "Find your building — street or city",
    label: null,
    note: null,
  },
  provider: {
    title: "Your buildings",
    one: "building",
    many: "buildings",
    lede:
      "The buildings you are answerable for. Private to you — this app has no sharing, so a portfolio is only ever your own.",
    empty:
      "Add the buildings you own or manage. The overview then answers them together: what binds the portfolio, which exemptions each building claims, and which missing fact is blocking the most answers.",
    search: "Add a building — street or city",
    label: "A name you use for it internally",
    note: "Anything you need to remember about it",
  },
  agency: {
    title: "Spot checks",
    one: "spot check",
    many: "spot checks",
    lede:
      "Addresses you want to keep an eye on. Your actual subject is the whole sample, which needs none of these — this is a shortlist, not a caseload.",
    empty:
      "Nothing pinned. The overview already covers all 500 sample addresses; pin one here when you want to watch a particular record rather than the aggregate.",
    search: "Pin an address — street or city",
    label: "Why you are watching it",
    note: "What you are waiting on",
  },
  advocate: {
    title: "Cases",
    one: "case",
    many: "cases",
    lede:
      "One address per matter. They are listed, never summed — each is a different person's situation. Private to you; this app has no sharing and no inbox.",
    empty:
      "Add the address a matter turns on. Each case then carries its own evidence: the rules that reach it, the span quoted from the source, and the citation to put in a letter.",
    search: "Add a case address — street or city",
    label: "Matter name or reference",
    note: "What the question is",
  },
};

/** What a tenancy agreement is called, and whether a unit has to be named.
 *
 * `perUnit` is the cardinality question again, one level down. A renter has
 * one home and one lease, and asking them which unit it is for is asking them
 * to restate their own address. A provider's building has as many agreements
 * as it has let units, and the unit is the only thing that tells them apart —
 * so for them the field is not optional furniture, it is the key.
 */
export interface ContractVocabulary {
  title: string;
  one: string;
  many: string;
  lede: string;
  add: string;
  perUnit: boolean;
}

export const CONTRACTS: Record<Role, ContractVocabulary> = {
  renter: {
    title: "Your lease",
    one: "lease",
    many: "leases",
    lede:
      "The agreement for the home you live in. Stored against your account and readable by nobody else — this app has no sharing, so there is no one to show it to by accident.",
    add: "Upload your lease",
    perUnit: false,
  },
  provider: {
    title: "Tenancy agreements",
    one: "agreement",
    many: "agreements",
    lede:
      "One per let unit. A thirty-two unit building has thirty-two agreements, and the unit is what tells them apart — so name it, and the list stays navigable.",
    add: "Add an agreement",
    perUnit: true,
  },
  agency: {
    title: "Agreements on file",
    one: "agreement",
    many: "agreements",
    lede:
      "Any agreement you hold for this record. Private to your account; nothing here is published and nothing is read by the evaluator.",
    add: "Attach an agreement",
    perUnit: true,
  },
  advocate: {
    title: "Agreements in this matter",
    one: "agreement",
    many: "agreements",
    lede:
      "The lease the matter turns on, and any other agreement for the same building. Stored against your account only.",
    add: "Attach an agreement",
    perUnit: true,
  },
};

/** The menu, per role.
 *
 * Tailoring the menu is not withholding anything: every page here is reachable
 * by typing its path, and the API would serve it either way. What a shorter
 * menu says is that a renter has no use for a 500-row jurisdiction audit, and
 * leaving it out of their way is the whole point of asking for the role.
 */
const OVERVIEW: NavItem = { href: "/home", label: "Overview" };
const RULES: NavItem = { href: "/rules", label: "Rules" };
const ADDRESSES: NavItem = { href: "/addresses", label: "Addresses" };
const CHANGES: NavItem = { href: "/changes", label: "Changes" };

const NAV_BY_ROLE: Record<Role, NavItem[]> = {
  // One building, in plain words. The sample-wide address audit is somebody
  // else's job and would only be noise here.
  renter: [OVERVIEW, { href: "/places", label: PLACES.renter.title }, RULES, CHANGES],
  // The portfolio, then the law it is measured against.
  provider: [OVERVIEW, { href: "/places", label: PLACES.provider.title }, RULES, CHANGES],
  // Coverage of the stock is the job, so the address table is a first-class
  // destination rather than a curiosity.
  agency: [OVERVIEW, ADDRESSES, RULES, CHANGES, { href: "/places", label: PLACES.agency.title }],
  // Cases first, then the record behind them — including the raw addresses,
  // which is where a new matter starts.
  advocate: [
    OVERVIEW,
    { href: "/places", label: PLACES.advocate.title },
    RULES,
    ADDRESSES,
    CHANGES,
  ],
};

export function navFor(role: Role): NavItem[] {
  return NAV_BY_ROLE[role];
}

/** What a checkout with no WorkOS environment can reach.
 *
 * There are no accounts in that mode and therefore no role, so the menu falls
 * back to the three corpus views — which need no account and read the same to
 * everybody.
 */
export const CORPUS_NAV: NavItem[] = [RULES, ADDRESSES, CHANGES];

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLE_SLUGS as readonly string[]).includes(value);
}
