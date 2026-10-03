/** Server-side API client.
 *
 * Server components talk to the backend directly (BACKEND_URL); the browser
 * goes through the Next.js rewrite at /api/*, so there is only ever one origin
 * as far as the browser is concerned.
 *
 * Every call carries the signed-in person's WorkOS access token when there is
 * a session. The API verifies that signature itself on every request, which is
 * the only thing standing between a stranger and the data — this module adding
 * the header is a convenience, not the access check.
 */

import "server-only";
import { withAuth } from "@workos-inc/authkit-nextjs";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8080";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The access token of the current request, or null.
 *
 * Null covers two different situations that need the same handling here: no
 * one is signed in, and no WorkOS environment is configured at all. In both
 * the call goes out without a header and the API decides — which is how a bare
 * checkout stays usable without inventing an identity for it.
 */
export async function sessionToken(): Promise<string | null> {
  try {
    const { accessToken } = await withAuth();
    return accessToken ?? null;
  } catch {
    return null;
  }
}

export async function api<T>(
  path: string,
  init?: RequestInit & { revalidate?: number; anonymous?: boolean },
): Promise<T> {
  const { revalidate = 0, anonymous = false, ...rest } = init ?? {};
  const token = anonymous ? null : await sessionToken();
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...rest.headers,
    },
    next: { revalidate },
  });
  if (!res.ok) {
    throw new ApiError(
      `${res.status} ${res.statusText}`.trim(),
      res.status,
      path,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Same as `api`, but returns null instead of throwing.
 *
 * Pages use this so a cold backend renders an honest "backend unreachable"
 * panel rather than a Next.js error screen - which matters when you are
 * demoing and the machine is still booting.
 */
export async function tryApi<T>(
  path: string,
  init?: RequestInit & { revalidate?: number; anonymous?: boolean },
): Promise<T | null> {
  try {
    return await api<T>(path, init);
  } catch {
    return null;
  }
}
