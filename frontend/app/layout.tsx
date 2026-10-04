import type { Metadata } from "next";
import Link from "next/link";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";
import { AccountMenu } from "@/components/account-menu";
import { NavLink } from "@/components/nav-link";
import { NAV } from "@/lib/roles";
import { displayName, viewer } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rental Housing Law Navigator",
  description:
    "Which housing rules apply to an address on a given date, and how do supplied law-change cases affect the answer?",
};

/** What a checkout with no WorkOS environment can reach: the corpus views,
 *  which need no account. The personal pages are in `NAV` but say for
 *  themselves that they need one. */
const OPEN_NAV = NAV.filter((item) =>
  ["/rules", "/addresses", "/changes"].includes(item.href),
);

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // One menu for all four roles, for now. The session is resolved here rather
  // than per page, because the nav shows who is signed in and what they signed
  // up as; `viewer()` is memoised per render.
  const seen = await viewer();
  const nav =
    seen.state === "ready" ? NAV : seen.state === "open" ? OPEN_NAV : [];

  return (
    <html lang="en">
      <body>
        <AuthKitProvider>
          <div className="app-shell">
            <header className="glass-navigation">
              <Link href={seen.state === "ready" ? "/home" : "/"} className="brand">
                Navigator<span className="brand-period">.</span>
              </Link>

              <nav>
                {nav.map((item) => (
                  <NavLink key={item.href} href={item.href} label={item.label} />
                ))}
                {seen.state === "ready" ? (
                  <AccountMenu
                    name={displayName(seen.user)}
                    roleLabel={seen.account.role_label}
                  />
                ) : (
                  <a className="nav-item" href="/api/docs">
                    API docs
                  </a>
                )}
              </nav>
            </header>

            <main className="app-main">{children}</main>

            <footer
              style={{
                maxWidth: 1200,
                margin: "0 auto",
                padding: "40px 8px 0",
                fontSize: 12,
                color: "#6b818d",
              }}
            >
              Not legal advice. Every answer cites the source text it came from.
            </footer>
          </div>
        </AuthKitProvider>
      </body>
    </html>
  );
}
