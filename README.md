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
| Extraction | Anthropic API, `claude-opus-5` | Module A only |

The browser only ever talks to one origin. Next.js rewrites `/api/*` to the
Fly backend, so there is no CORS and no cookie juggling.

The backend is a **persistent container, not a serverless function** — on
purpose. A full corpus extraction pass runs for many minutes, which no
serverless timeout survives, and Fly is configured `auto_stop_machines = "off"`
so a long run can't be stopped out from under it mid-pass.

## The three modules

Each is a package under `backend/app/modules/` with its own router, schemas and
service, mounted at its own prefix.

### Module A — rule extraction (`/api/a`)

Reads the 54 supplied corpus documents and emits records matching
`schema/rule_record.schema.json`. Extraction is automated end to end; nothing
is hand-coded.

Two guards matter for scoring:

- **Spans are verified.** Every record must carry a `quoted_span` that really
  occurs in its source document. We check it in code (whitespace- and
  case-insensitive, because the corpus is PDF-extracted) and **discard** rules
  that fail, so a hallucinated citation cannot reach the submission.
- **We assign `team_rule_id`,** not the model, so ids stay unique across runs.

`POST /api/a/extract` starts a background pass and returns a run id;
`GET /api/a/runs/{run_id}/stream` streams progress as server-sent events.

### Module B — address lookup (`/api/b`)

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

### Module C — change tracking (`/api/c`)

Runs the five supplied cases (T1–T5) by **replaying Module B's evaluator** at
the relevant dates, rather than hard-coding expected outcomes.

The tests name rules by the challenge's ids (`CA-ALG-01`, `HOB-ALG-01`, …)
while our records carry ours (`r-0001`). `CANONICAL_RULES` in
`change_tracking/service.py` bridges the two with an explicit selector table,
and `GET /api/c/canonical-rules` shows the mapping — so an unmatched id is
visible rather than silently producing an empty set.

## Quickstart

```bash
make setup                  # backend venv + frontend deps

cp .env.example backend/.env    # fill in DATABASE_URL (+ ANTHROPIC_API_KEY for Module A)
make seed                   # load 87 documents and 500 addresses
make dev-api                # :8080
make dev-web                # :3000  (separate terminal)
```

`make help` lists the rest. API docs at `/api/docs`.

Module A needs `ANTHROPIC_API_KEY`; without it those endpoints return 503 and
everything else works normally.

## Submission exports

Each module exports its submission file in the shape
`submission_templates/` specifies:

| Endpoint | File |
|---|---|
| `GET /api/a/rules/export` | `rules.json` |
| `GET /api/b/lookup/export` | `lookups.json` |
| `GET /api/c/export` | `changes.json` |

## Layout

```
backend/     FastAPI app, Dockerfile, fly.toml, migrations, tests
frontend/    Next.js app
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
