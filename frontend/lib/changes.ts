/** Reading a change case's answer — shared by server and client components.
 *
 * A blocked case comes back with empty sets, and an empty set is also what a
 * case that genuinely moves nobody looks like. Only `status` tells the two
 * apart, so nothing should read `affected_address_ids` to decide whether a
 * building was affected without going through `answered()` first.
 */

import type { ChangeStatus, ChangeTestResult } from "@/lib/types";

/** An API that predates per-case status only ever served complete cases. */
export function statusOf(result: ChangeTestResult): ChangeStatus {
  return result.status ?? "complete";
}

/** The cases whose sets are an answer. */
export function answered(results: ChangeTestResult[] | null | undefined): ChangeTestResult[] {
  return (results ?? []).filter((result) => statusOf(result) !== "blocked");
}

/** The cases that could not be computed, so a page can say its list is short. */
export function blocked(results: ChangeTestResult[] | null | undefined): ChangeTestResult[] {
  return (results ?? []).filter((result) => statusOf(result) === "blocked");
}
