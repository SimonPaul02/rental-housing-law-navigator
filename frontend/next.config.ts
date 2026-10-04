import type { NextConfig } from "next";
import { copyMapWorker } from "./scripts/copy-map-worker.mjs";

// Single-origin by design: the browser only ever talks to the Next.js host,
// and /api/* is proxied to the FastAPI backend on Fly. That keeps CORS and
// cookie handling out of the picture entirely.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8080";

// Staged here rather than from an npm script because this file is loaded by
// `next dev`, `next build` and Vercel alike, while a `prebuild` hook is
// skipped by anything that calls `next build` directly — which is what CI
// does. See scripts/copy-map-worker.mjs for why the worker needs staging.
copyMapWorker();

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
