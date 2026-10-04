/** Formatting shared by server and client components.
 *
 * It lives here rather than beside the component that first needed it because
 * a helper exported from a `"use client"` module cannot be *called* on the
 * server — only rendered or passed as a prop. A currency formatter is wanted
 * on both sides, so it belongs in neither.
 */

/** A rent, as a lease writes it. Stored in cents, because a rent held in
 *  floating point is a rent that drifts. */
export function money(cents: number): string {
  return (cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** A tenancy term, however much of it is known. */
export function term(range: {
  starts_on: string | null;
  ends_on: string | null;
}): string | null {
  if (range.starts_on && range.ends_on) return `${range.starts_on} to ${range.ends_on}`;
  if (range.starts_on) return `from ${range.starts_on}`;
  if (range.ends_on) return `until ${range.ends_on}`;
  return null;
}
