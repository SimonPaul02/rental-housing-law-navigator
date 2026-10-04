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

/** Upload a file.
 *
 * Separate from `clientApi` for one reason: the browser has to set
 * `Content-Type` itself on a multipart body, because only it knows the
 * boundary it generated. Sending `application/json` with a `FormData` — which
 * is what reusing the function above would do — produces a body the server
 * cannot parse and an error that looks like a server fault.
 */
export async function clientUpload<T>(path: string, form: FormData): Promise<T> {
  const token = await accessToken();
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (response.status === 401) forgetAccessToken();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string" ? body.detail : "The upload did not go through.",
    );
  }
  return (await response.json()) as T;
}
