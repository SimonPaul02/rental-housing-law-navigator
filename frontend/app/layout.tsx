import type { Metadata } from "next";
import Link from "next/link";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";
import { AccountMenu } from "@/components/account-menu";
import { Mark, Wordmark } from "@/components/brand";
import { NavLink } from "@/components/nav-link";
import { SignInCta } from "@/components/sign-in-cta";
import { CORPUS_NAV, navFor } from "@/lib/roles";
import { displayName, signInConfigured, viewer } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rental Housing Law Navigator — plua.ai",
  description:
    "Which housing rules apply to an address on a given date, and how do supplied law-change cases affect the answer?",
  icons: {
    // The SVG first for anything that can take it, the PNG for everything
    // else. Both are the brand kit's own files, served from public/brand.
    icon: [
      { url: "/brand/plua-app-icon.svg", type: "image/svg+xml" },
      { url: "/brand/favicon-32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/brand/plua-app-icon.svg",
  },
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
              {/* The logo, not the product name. "Rental Housing Law
                  Navigator" is what this thing does and it belongs in the
                  title and the hero; the header is where the brand goes. */}
              <Link
                href={seen.state === "ready" ? "/home" : "/"}
                className="brand"
                aria-label="plua.ai — home"
              >
                <Wordmark height={25} />
              </Link>

              <nav>
                {nav.map((item) => (
                  <NavLink key={item.href} href={item.href} label={item.label} />
                ))}
                {seen.state === "ready" ? (
                  <AccountMenu
                    name={displayName(seen.user)}
                    email={seen.account.email}
                    roleLabel={seen.account.role_label}
                  />
                ) : (
                  <>
                    <a className="nav-item" href="/api/docs">
                      API docs
                    </a>
                    {/* Signing in is its own page, so this is a link to it
                        rather than a jump down the front door. Absent when
                        there is nothing to sign in to, and on /login. */}
                    {signInConfigured && <SignInCta />}
                  </>
                )}
              </nav>
            </header>

            <main className="app-main">{children}</main>

            <footer className="app-footer">
              <Mark size={18} />
              <span>
                Not legal advice. Every answer cites the source text it came
                from.
              </span>
            </footer>
          </div>
        </AuthKitProvider>
      </body>
    </html>
  );
}
