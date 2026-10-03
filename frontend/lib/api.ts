/** Server-side API client.
 *
 * Server components talk to the backend directly (BACKEND_URL); the browser
 * goes through the Next.js rewrite at /api/*, so there is only ever one origin
 * as far as the browser is concerned.
 */

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

export async function api<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<T> {
  const { revalidate = 0, ...rest } = init ?? {};
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...rest,
    headers: { "Content-Type": "application/json", ...rest.headers },
    next: { revalidate },
  });
  if (!res.ok) {
    throw new ApiError(
      `${res.status} ${res.statusText}`.trim(),
      res.status,
      path,
    );
  }
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
  init?: RequestInit & { revalidate?: number },
): Promise<T | null> {
  try {
    return await api<T>(path, init);
  } catch {
    return null;
  }
}
