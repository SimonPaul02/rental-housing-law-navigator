import type { Metadata } from "next";
import Link from "next/link";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";
import { AccountMenu } from "@/components/account-menu";
import { ROLES } from "@/lib/roles";
import { displayName, viewer } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rental Housing Law Navigator",
  description:
    "Which housing rules apply to an address on a given date, and how do supplied law-change cases affect the answer?",
};

/** Navigation for a checkout with no WorkOS environment, where nobody has a
 *  role and the corpus views are open. */
const OPEN_NAV = [
  { href: "/rules", label: "Rules" },
  { href: "/addresses", label: "Addresses" },
  { href: "/changes", label: "Changes" },
];

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The menu is built from the role, which is why it is resolved here rather
  // than in each page: there is no navigation that fits all four apps, and a
  // menu offering pages a role cannot open would be a worse kind of wrong than
  // no menu at all. `viewer()` is memoised per render, so the page below this
  // asks the same question for free.
  const seen = await viewer();
  const nav =
    seen.state === "ready"
      ? ROLES[seen.role].nav
      : seen.state === "open"
        ? OPEN_NAV
        : [];

  return (
    <html lang="en">
      <body className="min-h-screen">
        <AuthKitProvider>
          <header
            className="sticky top-0 z-10 border-b backdrop-blur"
            style={{
              borderColor: "var(--border)",
              background: "color-mix(in srgb, var(--surface-0) 85%, transparent)",
            }}
          >
            <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
              <Link
                href={seen.state === "ready" ? "/home" : "/"}
                className="font-semibold tracking-tight"
              >
                Rental Housing Law Navigator
              </Link>

              <nav className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
                {nav.map((item) => (
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

              {seen.state === "ready" ? (
                <div className="ml-auto flex items-center gap-4">
                  <Link
                    href="/settings"
                    className="text-sm hover:underline"
                    style={{ color: "var(--text-muted)" }}
                  >
                    Settings
                  </Link>
                  <AccountMenu
                    name={displayName(seen.user)}
                    roleLabel={seen.account.role_label}
                  />
                </div>
              ) : (
                <a
                  href="/api/docs"
                  className="ml-auto text-sm hover:underline"
                  style={{ color: "var(--text-muted)" }}
                >
                  API docs
                </a>
              )}
            </div>
          </header>

          <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>

          <footer
            className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-sm"
            style={{ color: "var(--text-muted)" }}
          >
            Not legal advice. Every answer cites the source text it came from.
          </footer>
        </AuthKitProvider>
      </body>
    </html>
  );
}
