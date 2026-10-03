# Rental Housing Law Navigator

For any apartment address in the sample, answer: **which housing rules apply
here on the query date, and how do the supplied change cases affect that
answer?** Every answer cites the source text it came from.

MIT AI Hackathon entry. The challenge brief is in
[`docs/CHALLENGE.md`](docs/CHALLENGE.md).

> Not legal advice.

## Stack

| Layer | Choice | Where |
|---|---|---|
| Frontend | Next.js 15 (App Router, TypeScript, Tailwind 4) | Vercel |
| Backend | FastAPI + SQLAlchemy 2 (async) in Docker | Fly.io, always-on |
| Database | Postgres | Supabase |
| Accounts | WorkOS AuthKit (email + password, Google) | WorkOS |
| Extraction | Anthropic API, `claude-opus-5` | Module A only |

The browser only ever talks to one origin. Next.js rewrites `/api/*` to the
Fly backend, so there is no CORS and no cookie juggling.

The backend is a **persistent container, not a serverless function** — on
purpose. A full corpus extraction pass runs for many minutes, which no
serverless timeout survives, and Fly is configured `auto_stop_machines = "off"`
so a long run can't be stopped out from under it mid-pass.

## Sign in, and the four apps

People live in [WorkOS](https://dashboard.workos.com) and nowhere else. No
credential, no password reset and no Google connection exists in this
repository; the access token AuthKit issues *is* the session, and verifying its
signature against WorkOS's published keys is the API's entire job — so the
backend holds **no WorkOS secret**, only the public client id.

**There are no organisations.** Nobody shares a workspace with anybody else, so
there would be nothing for one to scope. Nothing in the app addresses another
account: there is no sharing, no invitation, no inbox, and `saved_places` has no
column that could point at a second person.

That simplification has one consequence that shapes the design. A WorkOS role
travels in an access token only as a property of an *organisation membership*,
so with no organisations there is no `role` claim to read. The role therefore
lives in our own `users` table, keyed by the token's `sub`:

| Role | Their app |
|---|---|
| `renter` | One address. Which rules cover the building they live in, and what each entitles them to. |
| `provider` | Their buildings. What binds each one, which exemptions it can claim, which fact is still missing. |
| `agency` | Coverage. How much of the housing stock the system can answer for, and where the records fail. |
| `advocate` | Evidence. The quoted span and citation behind every answer, and the conflicts still open. |

The four read the same record and ask entirely different things of it. The role
is resolved **server-side**, so a renter's browser never receives an agency's
markup and there is nothing to toggle. It is not a permission: every row this
API serves is either public corpus material or the caller's own, which is why
switching role is self-service — there is no administrator above an account to
ask. `lib/roles.ts` is the single table the picker, the navigation, the
dashboards and the route guards all read, so a role cannot be offered in one
place and unknown in another.

### Signing in happens in the page

The card asks for email and password; `/auth/sign-in` puts that to WorkOS
server-side and seals the answer into **the same cookie the hosted flow would
have written**, so nothing downstream can tell the two apart. Google is the one
exception — its consent screen is on Google's own domain by definition — and the
button goes straight there rather than through WorkOS's own screen.

Creating an account with a password **does** leave the page, deliberately: a new
address has to be verified, and the hosted form already is that state machine.
Google signs up and signs in with the same click.

### Setting it up

In the WorkOS dashboard pick the environment (staging for local work), copy
**WORKOS_CLIENT_ID** and **WORKOS_API_KEY** from *API Keys* into
`frontend/.env.local` (see `frontend/.env.local.example`), add
`WORKOS_COOKIE_PASSWORD` (`openssl rand -base64 32`), and put the **same client
id** into `backend/.env`. Under *Applications → Redirects* set:

| Field | Value |
|---|---|
| Redirect URI | `http://localhost:3000/callback` |
| Initiate login URI | `http://localhost:3000/sign-in` |
| Sign-out redirect | `http://localhost:3000` |

Use one host consistently. `localhost` and `127.0.0.1` are the same machine but
not the same origin, and a flow begun on the other name returns to a callback
with no cookie to verify — `/sign-in` and `/sign-up` bounce to the registered
host first so that cannot happen by accident.

Test accounts: `backend/scripts/workos_user.py test@rhln-local.dev` creates the
user with the address already marked verified (a made-up address never receives
a code), prints a generated password, and takes `--list` and `--delete`. There is
no membership to grant — the role is picked in the app on first sign-in. Avoid a
domain that is verified on a WorkOS organisation, or WorkOS requires SSO for it
instead of a password.

**Leaving `WORKOS_CLIENT_ID` empty is a supported mode outside production.** The
API stays open and the app shows no sign-in card, which is how a bare checkout
and the browser-free tests run; the role-specific views say plainly that they
need an account. The API refuses to serve without it when
`ENVIRONMENT=production`, so a forgotten client id turns everyone away rather
than publishing the data. `/api/health` reports `auth_required`, so the two
sides can never disagree about who has to sign in.

## The three modules

Each is a package under `backend/app/modules/` with its own router, schemas and
service, mounted at its own prefix.

### Module A — rule extraction (`/api/rule-extraction`)

Reads the 54 supplied corpus documents and emits records matching
`schema/rule_record.schema.json`. Extraction is automated end to end; nothing
is hand-coded.

Two guards matter for scoring:

- **Spans are verified.** Every record must carry a `quoted_span` that really
  occurs in its source document. We check it in code (whitespace- and
  case-insensitive, because the corpus is PDF-extracted) and **discard** rules
  that fail, so a hallucinated citation cannot reach the submission.
- **We assign `team_rule_id`,** not the model, so ids stay unique across runs.

`POST /api/rule-extraction/extract` starts a background pass and returns a run id;
`GET /api/rule-extraction/runs/{run_id}/stream` streams progress as server-sent events.

### Module B — address lookup (`/api/address-lookup`)

Resolves each address to its **legal** jurisdiction, then tests every rule's
coverage conditions against the building.

The mailing city is not the legal city. 37 rows in the sample are Boston
neighbourhoods (Dorchester, Roxbury, Allston, …) and one is San Ysidro, which
is the City of San Diego. Resolution goes through the Census geocoder's
incorporated-places layer, with a clearly-labelled postal fallback.

Coverage conditions are parsed into predicates and evaluated deterministically
(`coverage.py`). **"Unknown" is a first-class answer**, not a failure:

- a building in a certificate-of-occupancy **cutoff year** is unknown, because
  year built is not the certificate date (SF 1979-06-13, LA 1978-10-01);
- a missing unit count or year built is unknown, naming the field that blocked
  it;
- owner identity is absent from the data by design, so owner-type conditions
  are unknown.

Coverage conditions and exemptions are evaluated **separately**, because they
pull in opposite directions — an exemption matching means the rule does *not*
apply. That also lets a 32-unit building defeat a "2 or fewer units"
small-landlord exemption whoever owns it, which the brief asks for explicitly.

### Module C — change tracking (`/api/change-tracking`)

Runs the five supplied cases (T1–T5) by **replaying Module B's evaluator** at
the relevant dates, rather than hard-coding expected outcomes.

The tests name rules by the challenge's ids (`CA-ALG-01`, `HOB-ALG-01`, …)
while our records carry ours (`r-0001`). `CANONICAL_RULES` in
`change_tracking/service.py` bridges the two with an explicit selector table,
and `GET /api/change-tracking/canonical-rules` shows the mapping — so an unmatched id is
visible rather than silently producing an empty set.

## Quickstart

```bash
make setup                  # backend venv + frontend deps

cp .env.example backend/.env    # fill in DATABASE_URL (+ ANTHROPIC_API_KEY for Module A)
make migrate                # apply database migrations
make seed                   # load 87 documents and 500 addresses
make dev-api                # :8080
make dev-web                # :3000  (separate terminal)
```

For accounts, also `cp frontend/.env.local.example frontend/.env.local` and fill
in the WorkOS values; `make user EMAIL=you@rhln-local.dev` then creates a test
account to sign in with. Without them the app runs open — see
[Sign in](#sign-in-and-the-four-apps).

`make help` lists the rest. API docs at `/api/docs`.

Module A needs `ANTHROPIC_API_KEY`; without it those endpoints return 503 and
everything else works normally.

## Submission exports

Each module exports its submission file in the shape
`submission_templates/` specifies:

| Endpoint | File |
|---|---|
| `GET /api/rule-extraction/rules/export` | `rules.json` |
| `GET /api/address-lookup/lookup/export` | `lookups.json` |
| `GET /api/change-tracking/export` | `changes.json` |

## Layout

```
backend/     FastAPI app, Dockerfile, fly.toml, migrations, tests
  app/core/auth.py           WorkOS token verification (public keys only)
  app/modules/accounts/      the signed-in person and their saved buildings
frontend/    Next.js app
  middleware.ts              AuthKit session upkeep
  app/sign-in|sign-up/       the hosted flow, and the Google shortcut
  app/auth/                  the in-page sign-in, token handout, registration
  lib/roles.ts               the four roles, and what each one's app is
corpus/      87 source documents (54 with supplied text)
data/        500 sample addresses
schema/      rule_record.schema.json
dev/         change_tests.json (T1-T5)
docs/        the challenge brief
```

## Deployment

Backend deploys to Fly on every push to `main` that touches `backend/` or the
data directories (`.github/workflows/deploy-backend.yml`, needs the
`FLY_API_TOKEN` secret). Frontend is connected to Vercel's Git integration with
`BACKEND_URL` set to the Fly hostname.

The Docker build context is the **repo root**, not `backend/`, because the
image needs the corpus and address data alongside the app.

For accounts in production, point the frontend at the WorkOS **production**
environment — its own API key, client id and cookie password, set in the Vercel
dashboard — register the deployed `/callback`, `/sign-in` and sign-out URIs
there, and `fly secrets set WORKOS_CLIENT_ID=...` on the API. Set
`ENVIRONMENT=production` too: the API then refuses to serve without a client id
rather than publishing the data. The Vercel deployment needs the WorkOS
variables **at run time**, because signing in happens server-side inside it.
