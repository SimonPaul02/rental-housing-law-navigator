import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rental Housing Law Navigator",
  description:
    "Which housing rules apply to an address on a given date, and how do supplied law-change cases affect the answer?",
};

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/rules", label: "A · Rules" },
  { href: "/addresses", label: "B · Addresses" },
  { href: "/changes", label: "C · Changes" },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header
          className="sticky top-0 z-10 border-b backdrop-blur"
          style={{
            borderColor: "var(--border)",
            background: "color-mix(in srgb, var(--surface-0) 85%, transparent)",
          }}
        >
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              Rental Housing Law Navigator
            </Link>
            <nav className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="hover:underline"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
            <a
              href="/api/docs"
              className="ml-auto text-sm hover:underline"
              style={{ color: "var(--text-muted)" }}
            >
              API docs
            </a>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>

        <footer
          className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-sm"
          style={{ color: "var(--text-muted)" }}
        >
          Not legal advice. Every answer cites the source text it came from.
        </footer>
      </body>
    </html>
  );
}
