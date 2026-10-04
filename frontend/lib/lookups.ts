/** Evaluating the addresses one person saved, and reading the result.
 *
 * Shared by all four dashboards, because all four start from the same call and
 * then ask different questions of it. The questions are the part that differs;
 * the evaluation is the part that must not.
 */

import "server-only";
import { tryApi } from "./api";
import type { LookupResponse, Place, RuleCheck, RuleOutcome } from "./types";

/** Evaluate several saved addresses in one request.
 *
 * One POST rather than one GET per address: the batch endpoint compiles the
 * rule set once for the whole call, which is what makes a provider's portfolio
 * affordable to render on a single page. `persist: false` because a dashboard
 * render is a read — it should not write a lookup row as a side effect of
 * somebody opening a page.
 *
 * `includeNotApplicable` adds the rules that definitely do *not* bind each
 * address. The provider view is the one caller that needs them: "which
 * exemptions does this building claim" is only answerable from the rules an
 * exemption beat. They are never counted in `applies_count` — the API keeps
 * that arithmetic to the five reportable results.
 */
export async function evaluate(
  places: Place[],
  { includeNotApplicable = false }: { includeNotApplicable?: boolean } = {},
): Promise<Map<string, LookupResponse>> {
  if (places.length === 0) return new Map();
  const responses = await tryApi<LookupResponse[]>("/api/address-lookup/lookup", {
    method: "POST",
    body: JSON.stringify({
      address_ids: places.map((place) => place.address_id),
      persist: false,
      include_not_applicable: includeNotApplicable,
    }),
  });
  return new Map((responses ?? []).map((response) => [response.address_id, response]));
}

/** The outcomes of one result, split by what they are. */
export function split(lookup: LookupResponse | undefined) {
  const outcomes = lookup?.outcomes ?? [];
  return {
    applies: outcomes.filter((o) => o.result === "applies"),
    unknown: outcomes.filter((o) => o.result === "unknown"),
    superseded: outcomes.filter((o) => o.result === "superseded"),
    coming: outcomes.filter(
      (o) => o.result === "not_yet_effective" || o.result === "pending",
    ),
    notApplicable: outcomes.filter((o) => o.result === "does_not_apply"),
    conflicts: outcomes.filter((o) => o.conflict_flag),
  };
}

/** The exemption that beat a rule, when one did.
 *
 * A `does_not_apply` outcome whose exemption check came back true is a rule
 * the building is let off — which is the only way to answer a provider asking
 * what their building can claim. A rule that applies *despite* an exemption
 * has the same check at false, and that is worth saying too: it is how a
 * 32-unit building is shown defeating a small-landlord exemption.
 */
export function exemptionCheck(outcome: RuleOutcome): RuleCheck | undefined {
  return outcome.checks.find((check) => check.check === "exemption");
}

export function claimsExemption(outcome: RuleOutcome): boolean {
  return (
    outcome.result === "does_not_apply" && exemptionCheck(outcome)?.value === "true"
  );
}

/** Which fact is blocking the most answers, across a set of results.
 *
 * The provider's work queue, and the agency's. Ordered by how many rule
 * answers supplying one field would unblock, because that is the only ordering
 * that tells somebody what to go and find first.
 */
export function blockingFacts(
  lookups: Iterable<LookupResponse>,
): { field: string; blocked: number; addresses: string[] }[] {
  const tally = new Map<string, Set<string>>();
  const counts = new Map<string, number>();
  for (const lookup of lookups) {
    for (const outcome of lookup.outcomes) {
      if (outcome.result !== "unknown") continue;
      for (const field of outcome.unresolved_fields) {
        counts.set(field, (counts.get(field) ?? 0) + 1);
        const seen = tally.get(field) ?? new Set<string>();
        seen.add(lookup.address_id);
        tally.set(field, seen);
      }
    }
  }
  return [...counts.entries()]
    .map(([field, blocked]) => ({
      field,
      blocked,
      addresses: [...(tally.get(field) ?? [])].sort(),
    }))
    .sort((a, b) => b.blocked - a.blocked || a.field.localeCompare(b.field));
}

/** A field name as a person would say it. */
export function fieldName(field: string): string {
  return field.replace(/_/g, " ");
}

/** A category as a person would say it. */
export function categoryName(category: string | null): string {
  return (category ?? "uncategorised").replace(/_/g, " ");
}

/** The distinct categories across a set of outcomes, in a stable order. */
export function categoriesOf(outcomes: RuleOutcome[]): string[] {
  return [...new Set(outcomes.map((o) => o.category ?? "uncategorised"))].sort();
}
