/** One address, answered — the worked example on the front door.
 *
 * The headline asks which rules apply here, on this date. This is the claim
 * carried out: six obligations settled on one real building from the sample,
 * and the one span of statute the first of them rests on.
 *
 * It is a **recorded run, not a live query**, and the panel says so. Every
 * corpus endpoint needs an account, so a live specimen on the one page with
 * nobody signed in would mean either publishing the corpus to anonymous
 * callers or inventing an identity for the page to use.
 *
 * Copied from a real run, not written by hand: address A0494 in
 * data/resolved_addresses.json, answers from
 * data/lookup_runs/2026-10-01-20261004T025325Z.jsonl, joined to the rule
 * records the API serves at /api/rule-extraction/rules. Recopy from a later
 * run rather than editing it into agreement.
 */

export interface SpecimenRow {
  category: string;
  /** The answer, short enough to read at a glance. */
  answer: string;
  result: "applies" | "unknown";
}

export const SPECIMEN = {
  address: "2641 Franklin St",
  locality: "San Francisco, CA",
  asOf: "2026-10-01",

  rows: [
    { category: "Rent increase limits", answer: "1.6% of base rent", result: "applies" },
    { category: "Just cause eviction", answer: "17 enumerated reasons", result: "applies" },
    { category: "Security deposits", answer: "Returned within 21 days", result: "applies" },
    { category: "Application screening fees", answer: "Capped at $30 per applicant", result: "applies" },
    { category: "Algorithmic rent setting", answer: "Prohibited outright", result: "applies" },
    // The honest row, and the reason the panel is worth showing at all.
    { category: "Screening restrictions", answer: "Needs owner_occupied", result: "unknown" },
  ] satisfies SpecimenRow[],

  /** The span behind the first row. One quote, because one is the proof. */
  quote:
    "For rent-controlled units, the annual allowable increase amount effective March 1, 2026 through February 28, 2027 is 1.6%.",
  citation: "San Francisco Rent Board",
  sourceUrl: "https://www.sf.gov/news--annual-rent-increase-3126-22827-announced",
};
