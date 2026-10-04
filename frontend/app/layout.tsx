import type { Metadata } from "next";
import Link from "next/link";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";
import { AccountMenu } from "@/components/account-menu";
import { NavLink } from "@/components/nav-link";
import { CORPUS_NAV, navFor } from "@/lib/roles";
import { displayName, viewer } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rental Housing Law Navigator",
  description:
    "Which housing rules apply to an address on a given date, and how do supplied law-change cases affect the answer?",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The menu is the role's, so the session is resolved here rather than per
  // page: the nav both names who is signed in and is itself tailored to them.
  // `viewer()` is memoised per render, so the pages below pay nothing for it.
  //
  // A shorter menu withholds nothing. Every page is still reachable by path and
  // the API would serve it either way — what the role decides is what is put in
  // somebody's way, which is the whole reason it was asked for.
  const seen = await viewer();
  const nav =
    seen.state === "ready"
      ? navFor(seen.role)
      : seen.state === "open"
        ? CORPUS_NAV
        : [];

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
