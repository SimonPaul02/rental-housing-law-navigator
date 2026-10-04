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

/** Open a file the API will only hand over to a bearer token.
 *
 * `<a href="/api/.../file" target="_blank">` cannot work here and the failure
 * is a confusing one: a new tab is a plain navigation, it carries no
 * `Authorization` header, and the API answers `{"detail":"Please sign in."}`
 * to somebody who plainly is signed in.
 *
 * So the file is fetched with the token and opened from a blob instead. The
 * window is opened *before* the await, because a popup blocker allows one
 * opened in the click and blocks one opened a second later — and then pointed
 * at the blob once the bytes are here.
 *
 * The bytes still live in the database row. An object store would make the
 * link trivially shareable and would also be a second place a deleted lease
 * could survive its own deletion, which is the trade this app has already
 * made deliberately.
 */
export async function openFile(path: string, filename: string): Promise<void> {
  const tab = window.open("", "_blank");
  const token = await accessToken();
  const response = await fetch(`/api${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401) forgetAccessToken();
  if (!response.ok) {
    tab?.close();
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string" ? body.detail : "That file could not be opened.",
    );
  }

  const url = URL.createObjectURL(await response.blob());
  if (tab) {
    tab.location.href = url;
  } else {
    // The popup was blocked, so fall back to a download, which a click can
    // always do. Better a file in the downloads folder than a dead button.
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
  }
  // Long enough for the tab to have loaded it; the blob is held by this
  // document and would otherwise live until the page is closed.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
