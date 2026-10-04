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
  // than per page, because the sidebar shows who is signed in and what they
  // signed up as; `viewer()` is memoised per render.
  const seen = await viewer();
  const nav =
    seen.state === "ready" ? NAV : seen.state === "open" ? OPEN_NAV : [];

  return (
    <html lang="en">
      <body>
        <AuthKitProvider>
          <div className="app-shell">
            <aside className="sidebar">
              <Link href={seen.state === "ready" ? "/home" : "/"} className="brand">
                <span className="brand-mark" aria-hidden>
                  R
                </span>
                <span>
                  Navigator<span className="brand-period">.</span>
                </span>
              </Link>

              {nav.length > 0 && (
                <nav>
                  <p className="nav-caption">Navigate</p>
                  {nav.map((item) => (
                    <NavLink key={item.href} href={item.href} label={item.label} />
                  ))}
                </nav>
              )}

              <div className="sidebar-bottom">
                {seen.state === "ready" ? (
                  <AccountMenu
                    name={displayName(seen.user)}
                    roleLabel={seen.account.role_label}
                  />
                ) : (
                  <a className="nav-item" href="/api/docs">
                    <span className="nav-dot" aria-hidden />
                    API docs
                  </a>
                )}
                <p className="sidebar-note">
                  Not legal advice. Every answer cites the source text it came
                  from.
                </p>
              </div>
            </aside>

            <main className="app-main">{children}</main>
          </div>
        </AuthKitProvider>
      </body>
    </html>
  );
}
