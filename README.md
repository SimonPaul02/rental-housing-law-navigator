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
| Maps | MapLibre GL over CARTO Positron (no API key) | in the browser |
| Backend | FastAPI + SQLAlchemy 2 (async) in Docker | Fly.io, always-on |
| Database | Postgres | Supabase |
| Accounts | WorkOS AuthKit (email + password, Google) | WorkOS |
| Extraction | Anthropic API, `claude-opus-5` | Module A only |
| Assistant | LangGraph + Anthropic API, `claude-sonnet-5-5` | the overview page |

The browser only ever talks to one origin. Next.js rewrites `/api/*` to the
Fly backend, so there is no CORS and no cookie juggling.

The backend is a **persistent container, not a serverless function** — on
purpose. A full corpus extraction pass runs for many minutes, which no
serverless timeout survives, and Fly is configured `auto_stop_machines = "off"`
so a long run can't be stopped out from under it mid-pass.

## Sign in, and the four roles

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

**The four roles get four different apps**, and what separates them is not
styling. It is *how many addresses the person has*, which decides whether the
page can be about one building in depth, about a set in aggregate, or about
neither.

| Role | Addresses | The app they get |
|---|---|---|
| `renter` | **One.** The home they live in. A second is the flat they are considering or the one they just left — never a set to add up. | One building rendered deeply, in plain words: what applies today, what each rule entitles them to, what is still unsettled and which missing fact would settle it. Their lease sits beside the rule that governs their rent. No corpus statistic appears anywhere on it. |
| `provider` | **Many, and answerable for all at once.** | The portfolio *is* the page: a building × obligation matrix, a missing-fact queue ordered by how many answers each field would unblock, and the local rules that were tested and still do not bind. One building is the drill-down. |
| `agency` | **None at all**, and nowhere to keep one. Their subject is the whole sample. | The only view whose headline number is a denominator — how much of the stock can be answered, and what is stopping the rest. The only one complete on first sign-in, and the only role with no saved-address page: one map of the stock answers what a shortlist would, so `/places` sends them to it. |
| `advocate` | **Many, but they never add up** — each is a different person's situation. | Cases listed, never summed, each with the checks the evaluator ran to reach its answer. Flags raised while answering are kept separate from conflicts in the record, because they are different problems. |

The seam the four opened along is exactly where it was always going to be:
`navFor()` and `PLACES` in `frontend/lib/roles.ts`, and
`frontend/components/home/dashboard.tsx`, which is now a dispatcher over
`renter.tsx`, `provider.tsx`, `agency.tsx` and `advocate.tsx`.

`PLACES` and `CONTRACTS` are `Partial` records for that reason, and the absent
agency key is the statement: `PLACES.renter` is proven to exist by the type
system so the three views that have a page need no check, while `placesFor()`
returns `undefined` for a role resolved at run time, so the one page that has
to handle the absence cannot forget to.

A role is never a permission. Every row this API serves is either public corpus
material or the caller's own, which is why switching role is self-service —
there is no administrator above an account to ask, and the switch grants
nothing. A shorter menu withholds nothing either: every page stays reachable by
path and the API would serve it regardless. What the role decides is what is
put in somebody's way.

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

**One WorkOS environment serves both local work and the deployment.** One set of
keys, one pool of accounts — sign up locally and the same account works in
production. There is nothing to keep in sync and no second user space to
remember which one an address exists in.

Copy **WORKOS_CLIENT_ID** and **WORKOS_API_KEY** from *API Keys* into
`frontend/.env.local` (see `frontend/.env.local.example`), add
`WORKOS_COOKIE_PASSWORD` (`openssl rand -base64 32`), and put the **same client
id** into `backend/.env`. Under *Applications → Redirects*, register both hosts:

| Field | Local | Deployed |
|---|---|---|
| Redirect URI | `http://localhost:3000/callback` | `https://<host>/callback` |
| Initiate login URI | `http://localhost:3000/sign-in` | `https://<host>/sign-in` |
| Sign-out redirect | `http://localhost:3000` | `https://<host>` |

Both can be registered at once; what decides which one a flow uses is
`NEXT_PUBLIC_WORKOS_REDIRECT_URI`, set per deployment.

Within one deployment, use one host consistently. `localhost` and `127.0.0.1`
are the same machine but not the same origin, and a flow begun on the other name
returns to a callback with no cookie to verify — `/sign-in` and `/sign-up` bounce
to the host the redirect URI names so that cannot happen by accident.

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

## The overview page is an agent

`/home` opens on a conversation for all four roles. Ask it something and it
answers from this deployment's own data — which rules reach an address, which
field is blocking the rest, what the five change cases move — quoting the span
every answer was read from. It knows no housing law that is not in this corpus
and is told to say so rather than fill the gap.

**What it cannot read, it asks for, and the asking is a control in the chat.**
With nothing saved, "what applies to my home?" renders a search box that saves
the building; a lease is a file picker; a term or a rent is a small form. The
turn stops there and resumes when the control is used. So the answer and the
thing blocking the answer are in the same place, and nobody is sent to another
page to unblock a sentence.

Those controls are not forms the model invented. They post to the endpoints
that already exist — the same `POST /accounts/me/places` the buildings page
uses, the same multipart contracts route — so there is still exactly one write
path for a building and one for a document. And what comes back is a *claim*:
every resolution is re-read from the database through `accounts.service`,
which filters on the token's `sub`, and the sentence the model is then told is
written from the row. It is never told what the browser said happened.

The agent **proposes and the server decides** everywhere this matters. An ask
naming a building that is not the caller's comes back as a tool error it can
correct from, not as a file picker aimed at somebody else's building. A link
is checked against an allowlist of this app's own paths. Neither check is in
the browser, because neither could be.

### Four roles, four different agents

Not four tones of voice: the brief and the **roster of tools** differ, so what
each role can even be offered differs. An agency holds no addresses and has
nowhere in this app to keep one, so they have no tool that could propose
saving or uploading anything — the assistant cannot suggest it because it has
no way to. A renter gets no cross-building work queue, because with one home a
ranking is a sentence. Nothing here depends on a prompt politely declining.

### Spending as little as it can

The figure in the corner of the panel — `3,421 in · 3,413 cached · 197 out ·
$0.01` — is in the interface rather than a log because it is the only way to
see the caching working. Four things keep it there:

- **The prefix is frozen.** Tools render at position 0 of a request and the
  system prompt right after, so those bytes are the cache prefix and nothing
  that moves may appear in them — no name, no date, no building list, and no
  figure a tool also reports. `prompts.py` is all constants for that reason,
  and `test_system_prompt_is_identical_for_two_people` fails if that changes.
  Dynamic context goes into a user turn, where it invalidates nothing before
  it, and is re-sent only when it has actually changed.
- **The conversation caches too.** A second breakpoint moves to the newest turn
  on every request, so a long chat reads its own history back at a tenth of
  the price instead of re-paying for it.
- **Sonnet 5.5 at low effort.** A chat turn here reasons over results a tool
  has already computed; the careful statutory reading is Module A's job, at
  `extraction_model`. Raise `ASSISTANT_EFFORT` before reaching for a bigger
  model.
- **Results are cut to size.** A tool result is input tokens on every later
  turn, forever — so lists are capped and overflow is reported as a count, and
  `rules_for_building` deliberately omits the quoted spans. `rule_source`
  fetches one, for the one rule the assistant is about to quote.

### How little LangGraph it is

Two nodes. `think` calls the model and streams what it writes; `act` runs the
read tools and loops back — unless one of the calls is an ask, in which case it
renders the control and the turn ends with that `tool_use` block
**unanswered**. The next request supplies it as the `tool_result`, so the
transcript is a legal one at every point in between, which is the only reason
the pause works: the API refuses an assistant turn that follows an unanswered
call.

The model is called through the Anthropic SDK directly. LangGraph supplies the
graph and the checkpointer and nothing else — no provider adapter, so there is
no second place where a request body is built, and `cache_control` lands on the
exact content block it was meant for.

Transcripts live in the process, keyed by the token's `sub` and the browser's
thread id, so **the browser never sends history** — one message and an id. A
guessed id opens an empty conversation of its own. A deploy ends them, which is
honest for what this is: a conversation about a page, not a record of advice.

Without `ANTHROPIC_API_KEY` the panel says so and the rest of the page is
unaffected — it is read out of the database and needs no model. `/api/health`
reports `assistant_available` alongside `extraction_available`, because in
practice the two fail separately: a deployment can have imported its rules and
hold no key at all.

> Below the conversation, every role's dashboard is still there under **The
> record behind it** — the same data and the same figures, none of it produced
> by a model. An agent is a good way to ask and a poor way to scan five hundred
> rows.

## The map, and the table beside it

Every view that can be drawn geographically offers both, and **the toggle is a
rendering choice, never a different dataset**: whatever the filters leave is
what the map draws and what the table lists, down to the row. The table is also
the version that works with a screen reader, with the browser's own find, and
on paper — so the map is never the only way to reach something.

It is [MapLibre GL](https://maplibre.org) over CARTO's keyless Positron
basemap, overridable per deployment with `NEXT_PUBLIC_MAP_STYLE`. If the
basemap will not load the pins, the filters and the popups still work and the
legend says so; a borrowed canvas failing should not take the page with it.

Three honesty constraints shape it, and each is stated in the interface rather
than buried here:

- **A pin is the geocoder's own coordinate.** 474 of the 500 sample addresses
  have one. An unresolved address has none — and so does one a human resolved
  by override — so the map always places fewer rows than the table lists and
  reports the difference. Nothing is ever approximated onto the map.
- **A shaded jurisdiction is the extent of our sample, not a boundary.** We
  hold no city geometry. Highlighting Los Angeles draws the convex hull of the
  addresses we have there, labelled as exactly that. A city with fewer than
  three placed addresses is not shaded at all, and is left out of the legend
  rather than promising a shape that is not on the map.
- **A rule has a jurisdiction, not a coordinate.** The rules map draws one
  bubble per jurisdiction, area proportional to its rule count, at the mean of
  that jurisdiction's sample addresses. Rules in jurisdictions the sample does
  not reach — Santa Ana's five — are counted off to one side instead of being
  dropped, because a map that hides what it cannot draw makes coverage look
  better than it is.

Colour never carries meaning alone: every legend states each tone in words.
The map library is loaded on demand, so a page that opens on its table pays
nothing for a canvas nobody asked for.

MapLibre resolves its own worker through `import.meta.url`, which a bundler
rewrites — so `scripts/copy-map-worker.mjs` stages the worker into `public/`
and the component points at it. `next.config.ts` runs it, rather than an npm
hook, because CI calls `next build` directly.

## Tenancy agreements

A person can file the agreement for a building they have saved — the renter
their lease, the others one per let unit, which is how a thirty-two unit
building carries thirty-two. Private to its owner like everything else here:
`place_contracts` is filtered on the token's `sub` directly rather than through
the place, so a mistake in a join cannot widen the query, and a row belonging to
somebody else is reported as missing rather than forbidden.

The bytes live in the row. There is no object store in this deployment, a lease
is a few hundred kilobytes, and a second home for the data would be a second
way for a deleted lease to survive its own deletion — `DELETE` here is the whole
deletion. Uploads are checked against an allowlist of types, because the file is
served back from the same origin the app runs on: nothing a browser could
execute is storable, downloads carry `nosniff` and `private, no-store`, and only
a PDF or an image is shown in place.

**Nothing reads the file.** The term and the rent are typed in by the person who
uploaded it, are never verified against it, and are never used in a calculation.
They are shown *beside* the rule that governs them — a renter sees their
$1,850 next to "0% additional utility increase" — because extracting a figure
from somebody's own document and then telling them what they are owed would be
advice resting on an unverified reading. A rent cap worded "the lower of 5% +
CPI or 10%" also needs a CPI figure this corpus does not carry.

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

**A pass is extracted once and then moved, not re-run per environment.** The
model costs money and does not repeat itself exactly, so the pass that was
reviewed is the one that should be served. `POST /api/rule-extraction/rules/import`
takes a `rules.json` payload with `{"rules": [...], "replace": true}`:

```bash
curl -X POST https://<host>/api/rule-extraction/rules/import \
  -H 'Authorization: Bearer <access token>' -H 'Content-Type: application/json' \
  --data-binary @rules.json
```

The span guard runs **again** on the way in, against that deployment's own copy
of each source document, so an import cannot introduce a rule the corpus does
not support — anything that fails comes back in `rejected` instead of being
written. Ids are derived from the rule rather than from a counter, so importing
the same pass twice updates the rows in place. `replace` deletes the existing
rules first, and `lookups` cascades off them, so Module B needs re-running
afterwards (`POST /api/address-lookup/lookup`).

### Module B — address lookup (`/api/address-lookup`)

`GET /addresses` carries the coordinate and the verified legal city alongside
each row, and filters on state, legal city, jurisdiction status, ZIP
discrepancy, year-built and unit ranges, and whether a row can be mapped at
all — which is what the explorer's facets are built from. `GET /lookup/{id}`
and `POST /lookup` take `include_not_applicable`, which adds the rules that
definitively do *not* bind an address. It exists for one caller: a provider
asking what was tested and missed. Those outcomes are never persisted, never
exported, and never counted in `applies_count` or `unknown_count` — the
submission vocabulary stays the five results it has words for.


Resolves each address to its **legal** jurisdiction, then tests every rule's
coverage conditions against the building.

The mailing city is not the legal city. 37 rows in the sample are Boston
neighbourhoods (Dorchester, Roxbury, Allston, …) and one is San Ysidro, which
is the City of San Diego. The independent `address_resolution` package validates
Census incorporated-place candidates and preserves unresolved results for review.
It never substitutes a mailing city for a verified legal city. The supplied
Census response cache supports reproducible offline runs; documented review
overrides can be supplied through `data/address_overrides.csv`.

ZIP quality is tracked separately from jurisdiction: each accepted Census
endpoint contributes its ZIP to a structured assessment, and discrepancies are
exported in `data/zip_review.csv`. Source-backed human ZIP findings are stored
in a separate database audit table. Existing deployments can run
`cd backend && python3 scripts/backfill_zip_assessments.py` after migration to
add ZIP evidence from the checked-in cache without changing legal cities.

The separate `property_facts` package retains raw values, missingness, fact
kind and source provenance. Its status flags prevent an invalid or contradictory
unit count from silently determining rule coverage. Both packages live under
`backend/app/modules/address_lookup/`; the IO bridges live in `adapters/`.

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
[Sign in](#sign-in-and-the-four-roles).

**Put the same `WORKOS_CLIENT_ID` in `backend/.env`.** The frontend will sign
somebody in without it and the API will then refuse to say who they are, so
every role-specific page reports that the account could not be loaded while the
corpus views keep working — a confusing state to debug, and one setting away.

`make help` lists the rest. API docs at `/api/docs`.

Module A and the assistant both need `ANTHROPIC_API_KEY`; without it those
endpoints return 503, the overview page says the assistant is not configured,
and everything else works normally.

## Submission exports

Each module exports its submission file in the shape
`submission_templates/` specifies:

| Endpoint | File |
|---|---|
| `GET /api/rule-extraction/rules/export` | `rules.json` |
| `GET /api/address-lookup/lookup/export` | `lookups.json` |
| `GET /api/change-tracking/export` | `changes.json` |

`include_not_applicable` touches none of these: the exports are built from the
five reportable results, exactly as before.

Each change case is answered on its own and reports a `status`: `complete`,
`partial` (the affected set stands, but e.g. T3's conflict check could not run),
or `blocked` with a `blocked_reason`. `/results` always lists all five, so one
case that cannot be answered does not blank the others. `/export` still refuses
unless all five are complete, because a submission file may not be partial;
`?strict=false` gives a working copy that leaves a blocked case out — rather
than writing an empty list, which would claim nothing moved — and names it in
the `X-Changes-Omitted` header. `scripts/freeze_submission.py` is strict the
same way unless given `--allow-incomplete`.

## Layout

```
backend/     FastAPI app, Dockerfile, fly.toml, migrations, tests
  app/core/auth.py           WorkOS token verification (public keys only)
  app/modules/accounts/      the person, their buildings, their agreements
  app/modules/assistant/     the agent: frozen prompt, tools, two-node graph
frontend/    Next.js app
  middleware.ts              AuthKit session upkeep
  app/sign-in|sign-up/       the hosted flow, and the Google shortcut
  app/auth/                  the in-page sign-in, token handout, registration
  lib/roles.ts               the four roles: cardinality, menu, vocabulary
  lib/jurisdictions.ts       where a rule's jurisdiction sits, and why
  components/home/           dashboard.tsx mounts the agent, then dispatches
  components/assistant/      the chat, the in-chat controls, the SSE reader
  components/map/            MapLibre, the palette, and the hull
  components/explorer/       filters + map/table for addresses, rules, changes
  components/contracts.tsx   filing a tenancy agreement
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

Accounts use the **same WorkOS environment as local work**, so there are no
separate production keys — the same client id and API key, with the deployed
`/callback`, `/sign-in` and sign-out URIs registered alongside the local ones.
Two things carry them:

```bash
fly secrets set WORKOS_CLIENT_ID=client_... --app rhln-api
```

and, in the Vercel dashboard (Production), `WORKOS_CLIENT_ID`,
`WORKOS_API_KEY`, `WORKOS_COOKIE_PASSWORD` and
`NEXT_PUBLIC_WORKOS_REDIRECT_URI=https://<host>/callback`. Vercel needs them
**at run time**, not just at build, because signing in happens server-side
inside the Next.js process.

`fly.toml` already sets `ENVIRONMENT=production`, and that has a consequence
worth knowing before a deploy: without the client id the API **refuses every
request** rather than publishing the data. That is the intended guard, but
`/api/health` still answers 200 — so the Fly health check and the frontend smoke
test both pass while the API turns everyone away. Set the secret in the same
sitting as the deploy, or the deployment goes quietly dark.
