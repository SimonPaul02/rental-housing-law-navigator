import Link from "next/link";
import { redirect } from "next/navigation";
import { RoleGrid } from "@/components/role-grid";
import { SignIn } from "@/components/sign-in";
import { signInConfigured, viewer } from "@/lib/auth";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Sign in — Rental Housing Law Navigator",
};

/** Signing in, on a page of its own.
 *
 * It used to be a card at the foot of the front door, which put the one thing
 * a returning person came for behind the whole pitch. A marketing page sells;
 * this page lets you in. Keeping them apart is also what lets the front door
 * be laid out for a first visit rather than for a daily one.
 *
 * The four demo accounts live here rather than on the front door for the same
 * reason: they are a way in, not an argument.
 */
export default async function Login() {
  const seen = await viewer();
  if (seen.state === "ready") redirect("/home");
  if (seen.state === "unregistered") redirect("/welcome");

  return (
    <div className="login">
      <div className="login-head">
        <h1>Sign in</h1>
        <p>
          {signInConfigured
            ? "Accounts live in WorkOS. Google works for signing up and signing in."
            : "This checkout runs without accounts."}
        </p>
      </div>

      <div className="login-card">
        <SignIn configured={signInConfigured} />
      </div>

      {signInConfigured && (
        <div className="login-demo">
          <p className="login-rule">
            <span>or sign in as one of the four roles</span>
          </p>
          <RoleGrid configured />
          <p className="login-note">
            Four different apps, so seeing all of them means being four
            different people. Ordinary accounts, nothing withheld.
          </p>
        </div>
      )}

      <p className="login-back">
        <Link href="/">← Back to the overview</Link>
      </p>
    </div>
  );
}
