"use client";

/** API client for client components.
 *
 * Same single origin as everything else — /api/* is a Next.js rewrite to
 * FastAPI — with the bearer token fetched per request from /auth/token and
 * held only in memory. See lib/session.ts.
 */

import { accessToken, forgetAccessToken } from "./session";

export async function clientApi<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = await accessToken();
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (response.status === 401) {
    // The token we hold is dead; drop it so the next call fetches a live one
    // rather than replaying the same rejection.
    forgetAccessToken();
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "The request did not go through.");
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
