import type { NextConfig } from "next";

// Single-origin by design: the browser only ever talks to the Next.js host,
// and /api/* is proxied to the FastAPI backend on Fly. That keeps CORS and
// cookie handling out of the picture entirely.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8080";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
