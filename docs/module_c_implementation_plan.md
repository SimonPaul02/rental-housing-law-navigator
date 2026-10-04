# Module C implementation plan: change tracking

**Purpose.** For each supplied change case, use cited rules from Module A and the verified address evidence and evaluator from Module B to report the affected sample addresses, temporal behavior, and conflicts. Produce a reproducible `changes.json` and a usable before/after explanation in the app. This is a plan, not a claim that the missing source records or test outcomes are already fixed.

**Scope decision.** The participant v5 brief ([PDF](../mit-rental-housing-law-navigator-challenge-v5-participant-no-scoring-no-hour16.pdf), pp. 2, 4, 6) defines **T1–T5** and explicitly says there is no mid-event document. The other [challenge brief](../challenge.pdf) and the older [B/C notes](modules_b_c.md) mention a fictional T6. Treat T1–T5 in [`dev/change_tests.json`](../dev/change_tests.json) as the required deliverable. Keep the engine able to process another test definition, but spend no time implementing a T6-specific path unless organizers actually supply one.

## 1. Starting point and boundaries

### What is in the repository

- [`backend/app/modules/change_tracking/service.py`](../backend/app/modules/change_tracking/service.py) already loads the five definitions, resolves challenge IDs through `CANONICAL_RULES`, and handles `as_of`, `boundary`, `pending`, and `negative` cases. It calls Module B's compiled-rule evaluator rather than using fixed answer lists.
- [`backend/app/modules/change_tracking/router.py`](../backend/app/modules/change_tracking/router.py) exposes test, run, result, mapping, stats, and export routes. [`frontend/app/changes/page.tsx`](../frontend/app/changes/page.tsx) displays expected behavior, counts, results, and unmatched IDs.
- Module B's [`evaluate_rule_for_address`](../backend/app/modules/address_lookup/service.py) returns a **single-rule base result**; its docstring explicitly says it does not run the interaction pass. The full `decide_for_address` path does run interactions. This distinction matters when describing a before/after **rule set** or an unresolved state/local conflict.
- [`pipeline/fetch.py`](../backend/app/modules/rule_extraction/pipeline/fetch.py), [`pipeline/links.py`](../backend/app/modules/rule_extraction/pipeline/links.py), inventory, cache, merge, and audit helpers exist, but the current [`extract_document`](../backend/app/modules/rule_extraction/service.py) requires `Document.body` and the extraction routes reject link-only documents. The source-ingestion path must be connected before local ordinances can appear.
- There is no `backend/tests/test_change_tracking.py` in the checkout. `ChangeResult` stores results by `(test_id, as_of)`, while `/export` reads all stored rows and lets later rows overwrite the same test ID. A previous partial or stale run can therefore look like a current submission.

The colleague's attached report is a **diagnostic snapshot**, not a verified current database state: it reports T1/T2/T4 empty, T3's temporal set populated, T5 empty, and five canonical IDs unmatched. Before changing code, remeasure the environment being submitted. In the checked-in sample CSV there are **500 addresses**: CA 250, NJ 140, MA 110; NJ's postal-city groups are Jersey City 50, Hoboken 40, Newark 50. Use verified legal cities and evaluator output for final assertions. In particular, investigate the colleague's approximate T1 count of 241 against the 250 CA input rows instead of encoding either number as an answer.

### Invariants to keep while Module B is being refined

1. Module C consumes the same rule version, compiled revision, address evidence, and as-of evaluator as Module B. No duplicate legal predicates or hard-coded address lists.
2. A cited source supports each **legal claim**; the challenge brief is the acceptance specification, not a substitute for a statute, ordinance, bill, or decision. Keep URL, document ID, retrieval date, quoted span, and the review decision together.
3. `pending`, `not_yet_effective`, and `failed` remain distinct. A hypothetical “if enacted” set never becomes an `applies` result in normal lookups. Unknown building facts stay unknown.
4. Rule imports and compilation changes can invalidate persisted lookups and change results. Coordinate on the rule and compiled-review files with the colleague refining B; freeze a rule snapshot before final validation.
5. This prototype is not legal advice. Flag unresolved preemption and weak or blocked source access for human review.

## 2. Target outputs and definitions

The submission contract is [`submission_templates/changes.json`](../submission_templates/changes.json): each test ID maps to sorted `affected_address_ids`, optional `conflict_flag_address_ids`, and `notes`. The app should retain richer detail for judges and users: the before and after query dates, the matching extracted rule IDs, the per-address before/after result and rule-set delta, source citations, and why an address is unknown or flagged. Keep that richer detail out of the strict export unless the official template changes.

| Case | Required semantic result | Expected coverage sanity check* |
|---|---|---|
| **T1** CA AB 325 / SB 763 | Same CA rule(s) are `not_yet_effective` on 2025-12-31 and live on 2026-01-02; distinguish `applies` from coverage `unknown`. | Compare with all 250 CA rows; inspect every exclusion or unknown. |
| **T2** Hoboken / Jersey City | Each local ban reaches only its verified legal city; neither reaches Newark. | Hoboken 40 and Jersey City 50 are candidate groups, not a hard-coded export. |
| **T3** NJ FAIR Act | State law is future on 2026-10-01 and live on 2027-07-02. Flag possible overlap with both local bans; do not decide preemption. | Inspect all 140 NJ rows; candidate conflict groups are the two local cities. |
| **T4** MA bills | S.2983 and H.5222 remain `pending`; compute a clearly labeled counterfactual for each bill, then their union. | Compare with all 110 MA rows; do not infer coverage solely from a bill title. |
| **T5** MA ballot | Record the measure as failed/struck and export `[]`; ensure no enacted rent cap appears for Boston or Cambridge. | Zero affected; a missing measure record is a validation failure, not a pass. |

\* These are review counts from [`data/sample_addresses.csv`](../data/sample_addresses.csv), not judge-key assertions. A missing property fact can make coverage unknown even when geography matches.

## 3. Ordered implementation steps

### Step 0 — Capture a reproducible baseline before edits (P0)

**Work:** In a local or staging database matching the intended submission, record the commit SHA, migrations, source corpus hash, rule count/statuses, canonical mapping, 500-address jurisdiction breakdown, compiled-review version, and the current T1–T5 results. Inspect `/api/change-tracking/canonical-rules`, `/api/change-tracking/results`, and `/api/rule-extraction/rules?doc_id=...` for D022, D045, and D046. Export current `rules.json` and `lookups.json` as backups before any import or replacement; do not put access tokens in the snapshot. Confirm that the colleague's reported gaps still exist. Use `persist=false` for exploratory reruns.

**Deliverable:** A short run record under `data/` or `docs/` with source hashes and per-test counts, plus a list of unresolved canonical IDs and why each lacks a record. No production mutation is needed for this step.

**Pass condition:** We can distinguish a missing rule, a mapping miss, an evaluator result of `unknown`, and a genuinely empty affected set. All 500 sample IDs are present in the baseline inventory.

### Step 1 — Make source ingestion usable for the missing local and negative cases (P0)

**Work:** Add a bounded ingestion command/service around the existing `pipeline.links.preflight`, `FetchBot`, inventory, citation check, `ExtractionCache`, and audit helpers. Target only named document IDs, starting with D032–D034 (Hoboken code), D035/D037 (Jersey City leads), and D055/D059 (MA ballot leads). Keep the preflight decision, HTTP result, source URL, retrieval timestamp, content hash, and extracted text or a precise coverage gap. Fetch one allowed URL at a time within the bot's rate limits; do not bypass robots, a 403, a login wall, or publisher terms. Prefer an official ordinance or decision if a news or law-firm page does not contain operative text. If an allowed source outside the manifest is required, add it to the manifest with a stable ID and provenance rather than silently swapping a URL.

Persist successful text in `Document.body` or a versioned corpus snapshot with its `origin`, `content_hash`, classification, and audit entry. Ensure re-seeding does not erase reviewed fetched text. Run Module A extraction only on the new or changed documents, verify every quoted span against the exact stored body, and review official/code text before lower-tier commentary when both describe one law. Keep a source blocked or unverified if the substantive ordinance or ruling cannot be obtained lawfully.

**Files likely touched:** `backend/app/modules/rule_extraction/service.py`, `router.py` or a new targeted ingestion script, `pipeline/{fetch,links,inventory,audit,merge}.py`, `backend/scripts/seed.py`, and tests around the new boundary.

**Pass condition:** Each targeted source has either a reproducible, span-verifiable body and extraction result or an audited “unavailable” reason. A source page with only a headline does not generate an ordinance. Repeating ingestion is idempotent and does not duplicate rules or pay for an unchanged model call.

### Step 2 — Repair the T1 effective-date evidence and replay (P0; small, first scored win)

**Work:** Inspect both California rules derived from D022 and any separate SB 763 record. D022's supplied AB 325 text has the statutory prohibition and chaptering information, but its body does not itself state the January 1 effective date. Locate a dated authoritative source or a properly justified statutory effective-date basis; store that evidence and a reviewer decision separately from the quoted obligation. Set `effective_date=2026-01-01` on each relevant extracted/merged rule **only after** verifying its provenance. Do not alter its obligation quote to make it appear to contain a date. Preserve the current `team_rule_id` where the legal obligation is the same, invalidate its compiled version, and rerun lookup and T1 snapshots at 2025-12-31 and 2026-01-02.

**Files likely touched:** Module A rule review/merge path or a provenance-aware correction file, `data/compiled_rules.json` if a compiled revision changes, and a T1 fixture in `backend/tests/test_change_tracking.py`.

**Pass condition:** A CA address in scope has a documented `not_yet_effective` → `applies` or `unknown` transition; no non-CA address is included; the mapped CA canonical ID has a nonempty, specifically reviewed rule set. Explain any CA rows excluded from the affected set. A later Module A import must not silently erase the date correction.

### Step 3 — Extract and distinguish the two city bans for T2 (P0)

**Work:** Use the Step 1 sources to produce cited Hoboken and Jersey City algorithmic-pricing records. Verify the actual ordinance title/chapter/section and effective date against source text; the colleague's note and the brief use different Hoboken chapter labels, so do not hard-code either label from memory. Confirm both extracted records have `level=city`, canonical jurisdiction strings `Hoboken, NJ` and `Jersey City, NJ`, correct status/date, and source-supported coverage and exemptions. Compile through B's rule adapter and review any unmapped clauses. Run candidate addresses from all three NJ cities, including a mailing-city/legal-city mismatch or unresolved-city fixture.

**Files likely touched:** source-ingestion/extraction and compiled-review data; `change_tracking/service.py` only for robust mapping and result semantics.

**Pass condition:** The two canonical IDs resolve separately; Hoboken rule has no Jersey City/Newark `applies`, Jersey City rule has no Hoboken/Newark `applies`, and neither appears as `applies` in Newark lookups. Any unresolved legal city remains `unknown`, never guessed from `postal_city`. The T2 export is the union of the per-rule address sets, with per-rule sets visible in detail.

### Step 4 — Make canonical matching exact and fail on missing or ambiguous inputs (P0)

**Work:** Replace the two identical MA pending selectors and other broad selectors with source-aware definitions: specific bill/document ID plus jurisdiction, category, status, and where needed a citation or reviewed legal-act key. Keep multiple records only when they are distinct provisions of the **same** act; report the grouping explicitly. In `resolve_canonical`, reject zero matches for a required case and reject matches from a different act. Detect one extracted rule assigned to both MA bill IDs. Surface these validation errors in the API/UI and prevent a “green” export. Review whether a source document can have several valid obligations without a one-to-one rule assumption.

**Files likely touched:** `backend/app/modules/change_tracking/service.py`, `schemas.py`, and tests.

**Pass condition:** MA-ALG-P1 maps only to S.2983, MA-ALG-P2 only to H.5222, and MA-RENT-P1 only to the failed ballot measure. Missing or ambiguous mappings produce actionable errors, not silent empty arrays.

### Step 5 — Use actual bill text for T4's counterfactual (P0)

**Work:** D045 and D046 are official status pages with titles and referral history, not operative bill text. Retrieve each bill's official “View Text” or another permitted public full-text copy and record the exact version, date, URL, retrieval time, and source body. Use those pages to establish `pending` status and the bill text to extract the proposed prohibition and its coverage. Keep each bill separate through extraction, canonical mapping, compiled review, and result detail. In the existing `_in_scope` counterfactual, remove **only** the temporal/status gate; preserve geography, coverage, exemptions, and unknowns. Use a named `as_of` from the test, rather than the current hard-coded date in `_in_scope`. Report three sets: P1, P2, and their union; export the union for T4. Never surface these as current `applies` in Module B.

**Files likely touched:** targeted source-ingestion path, `change_tracking/service.py`, `data/compiled_rules.json` review entries if needed, and tests.

**Pass condition:** Both canonical bill IDs resolve to cited, pending records; a 2026-10-01 normal lookup says `pending` or excludes a definitely uncovered address; the counterfactual can explain why each MA address is included, excluded, or uncertain. An official status page alone cannot produce a confident coverage claim.

### Step 6 — Give T5 positive evidence for the negative answer (P0)

**Work:** Obtain a permitted source for the measure's identity and its struck/failed disposition, ideally an official court or ballot record; use D055/D059 as leads/corroboration where needed. Record a `failed` measure linked to that evidence, with an exact measure ID and cited span. Ensure the normal evaluator omits it on every as-of date; do not count the statewide prohibition of local rent control as a “rent cap.” In the negative-case executor, explicitly require the failed record and scan **all current lookups for Boston and Cambridge** for any `rent_increase_limits` record that purports to impose a cap. Treat a missing record, a non-failed record, or a live local cap as an error. Keep `affected_address_ids=[]` only when those checks pass.

**Files likely touched:** source ingestion, `change_tracking/service.py`, export validator, and tests.

**Pass condition:** T5 has a verified failed measure, an empty affected set, and a nonvacuous no-rent-cap assertion across 110 MA sample rows. The app explains the negative finding with evidence, not merely “0 affected.”

### Step 7 — Complete replay semantics and the before/after rule-set view (P0)

**Work:** Keep the pure per-rule evaluator for temporal gates, but use B's full `decide_for_address` when presenting the **address's rule set** at each date. This includes reviewed interactions and `superseded` outcomes. For each as-of test, retain per-address entries with `before_result`, `after_result`, `team_rule_id`, source citation, and a reason code. Separate “date gate opened,” “confirmed covered after,” and “coverage unknown after”; the export affected set can include possible coverage if that is what the test requires, while the detail must not call unknown `applies`. For T3, flag only addresses whose verified legal city is Jersey City or Hoboken and where a local ban is actually applicable or potentially applicable under documented unknown facts. Record the flag as **possible preemption for human review**, not a conclusion that the state law supersedes the ordinance. Compute conflicts against the test's defined current/future dates and show which date was used.

**Files likely touched:** `change_tracking/service.py`, `schemas.py`, `frontend/app/changes/page.tsx`; perhaps a small shared helper in Module B if its full decision function is not conveniently reusable.

**Pass condition:** A detailed T1/T3 response demonstrates the two rule sets and transition, including `unknown` where facts are missing; T3 flags do not leak to Newark or other states. The submission format remains exactly the official template.

### Step 8 — Make runs, export, and audit reproducible (P0)

**Work:** Create one explicit Module C run or snapshot containing all five cases, source/rule/compiled/address version hashes, default as-of, per-test dates, start/completion state, and validation report. Prefer a small `change_runs` table plus `run_id` on results; if time is tight, an immutable JSON snapshot and a pointer to the latest validated run is sufficient. Never assemble `/export` from unrelated `(test_id, as_of)` rows. Do not export a partial `address_limit` run; reserve that parameter for exploration. Sort and deduplicate IDs; include all five test keys; reject stale results when rule or address inputs change. Append audit events for canonical matches, evaluated dates, affected and conflict decisions, and source gaps. Save a frozen `changes.json` next to `rules.json` and `lookups.json` for submission only after validation.

**Files likely touched:** `change_tracking/{service,router,schemas}.py`, `backend/app/db/models.py` plus Alembic migration if using DB snapshots, and a small export validator/CLI.

**Pass condition:** Replaying the same frozen inputs yields byte-identical `changes.json`. `/export` returns the last **complete validated** snapshot or a clear error; it cannot return `{}` or mix runs after an input change. Every affected ID exists in the 500-address sample and every referenced team rule exists in the same `rules.json` snapshot.

### Step 9 — Add Module C tests that prove behavior (P0)

**Work:** Add focused fixtures in `backend/tests/test_change_tracking.py` using small synthetic rules/addresses to verify T1's before/after gate, T2's three-city boundary, T3's future date and possible conflict, T4's pending/current versus counterfactual distinction, and T5's failed-record guardrail. Include missing canonical rules, duplicate MA bill matching, unresolved legal city, unknown coverage, partial/stale export, and a B interaction that changes a before/after rule set. Keep an integration test that runs the five definitions against a frozen local seed when DB access is available; the pure cases should run in normal CI without live network, model, or production DB. Run `make test` and `make lint`, then validate the three frozen submission files together.

**Pass condition:** The negative and missing-input fixtures fail for the right reason, and rerunning the test suite after Module B's colleague changes still gives the same Module C result for unchanged evidence.

### Step 10 — Finish the app and demo path (P1, after export correctness)

**Work:** Update the changes page to show: case status and as-of date; each bill/ordinance's resolved rule and source; before/after status for a selected address; the affected count and inspectable ID list; conflict flags and their exact reason; and source gaps or stale-run warnings. Label counterfactual T4 results “if enacted.” Label T5 as a documented failed measure. Keep the “not legal advice” notice consistent with other pages. A demo should be able to start with a live Module B address lookup, change the date, then show that the corresponding Module C case uses the same evidence and result vocabulary. Include a short method note describing source capture, cache, compiled rule review, and replay.

**Pass condition:** A reviewer can trace one address in each case from source quote to rule version to Module B decision to Module C set membership, without relying on an unexplained count.

## 4. Suggested execution order and checkpoints

| Checkpoint | Do first | Gate before moving on |
|---|---|---|
| **A. Baseline and quick temporal fix** | Steps 0, 2, then a focused T1 test | T1 transitions are evidence-backed; no regression in B. |
| **B. Local-law inputs** | Steps 1, 3, 4 | Both city rules resolve uniquely; T2 boundary guardrails pass. |
| **C. Remaining cases** | Steps 5, 6, then T3 conflict review in Step 7 | T3 flags, T4 per-bill pending/counterfactual, T5 nonvacuous empty set. |
| **D. Submission gate** | Steps 8, 9, 10 | One validated T1–T5 snapshot, all three JSON files frozen, UI trace and tests pass. |

The colleague refining Module B should own changes to shared compilation/evaluation internals. Agree on a small contract (`compiled_for`, `evidence_for`, `decide_for_address`, rule-version hash), then keep Module C edits in its service/router/tests and reviewed source data as much as possible. Rebase or re-run the baseline after any B change that alters those results. Do not run `rules/import` with `replace=true` on a shared database until its reviewed rule snapshot and lookups have been backed up: that path deletes existing rules and their dependent lookups.

## 5. Final acceptance checklist

- [ ] Five test IDs T1–T5 are present in one validated `changes.json`; no partial or stale export can masquerade as complete.
- [ ] All required canonical IDs resolve to source-backed, correctly distinct acts or bills; missing inputs fail visibly.
- [ ] T1 and T3 show evidence-backed before/after transitions and distinguish `applies` from `unknown`.
- [ ] T2 city bans do not bleed across legal city boundaries or into Newark.
- [ ] T3's potential state/local conflict is flagged for human review with source and date, without asserting preemption.
- [ ] T4's two bills are pending in ordinary lookups and separately evidenced in the “if enacted” calculation.
- [ ] T5's failed measure exists in the record, exports an empty affected set, and no Boston/Cambridge rent cap is reported.
- [ ] Every change ID is in the sample; every matched rule ID appears in the frozen `rules.json`; all 500 addresses appear in `lookups.json`.
- [ ] `make test` and `make lint` pass; a replay of frozen inputs gives identical output.
- [ ] The app shows the as-of date, citations/retrieval dates, uncertainty, conflict reason, and “not legal advice.”

**Source of requirements:** [participant v5 brief](../mit-rental-housing-law-navigator-challenge-v5-participant-no-scoring-no-hour16.pdf), [`docs/CHALLENGE.md`](CHALLENGE.md), [`dev/change_tests.json`](../dev/change_tests.json), and the challenge JSON templates. Legal facts must still be verified against the captured primary sources before they are entered as rule records.
