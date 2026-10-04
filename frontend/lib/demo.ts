import { ROLES, type Role } from "./roles";

/** The four published demo accounts, one per role.
 *
 * The four roles get four different apps, so "see all four" means signing in
 * as four different people. These exist so a reviewer can do that without
 * being issued anything: the credentials are on the front door.
 *
 * The passwords are in the client bundle on purpose. Anyone who can use the
 * buttons can read them anyway, so pretending otherwise would buy nothing and
 * cost the reviewer the ability to type them into the form or use them from a
 * script. They are demo accounts and are treated as public.
 *
 * They are ordinary accounts with no special powers and no restrictions - they
 * can do everything a signed-in person can do, which is the point of a demo.
 * Each one already has its role registered, so signing in lands on /home rather
 * than the role picker.
 */
export interface DemoAccount {
  role: Role;
  email: string;
  password: string;
}

export const DEMO_ACCOUNTS: DemoAccount[] = [
  { role: "renter", email: "renter@rhln-demo.dev", password: "Rhln-Demo-Renter-HpBWWA26" },
  { role: "provider", email: "provider@rhln-demo.dev", password: "Rhln-Demo-Provider-wiUu8dRu" },
  { role: "agency", email: "agency@rhln-demo.dev", password: "Rhln-Demo-Agency-MlKfT2WT" },
  { role: "advocate", email: "advocate@rhln-demo.dev", password: "Rhln-Demo-Advocate-aIolKwmv" },
];

export const demoLabel = (account: DemoAccount) => ROLES[account.role].label;
