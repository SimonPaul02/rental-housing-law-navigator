# Module C unblock plan: items 1–5

**Purpose.** Get from "no `changes.json` can be produced" to a partial-credit export plus a working changes page, and unblock T1, T3, T4 and T5 on their own. This follows the evaluation of 2026-10-04. It does not replace the [implementation plan](module_c_implementation_plan.md) or the [runbook](module_c_runbook.md); it changes the order of work and fixes three gaps the original plan missed.

**Out of scope here:** fetching the Jersey City and Hoboken sources (D088, D093). Until that runs, T2 stays blocked and T3 exports without its conflict flags. The evaluation listed it as item 6.

## Where it stands, and where 1–5 take it

| Test | Today | After 1–5 | What it still needs |
|---|---|---|---|
| T1 CA AB 325 | empty: the date compiles to unresolved, so the rule is `unknown` on both dates | **complete**, about 250 CA addresses | D092 captured (one fetch), then the date decision applied |
| T2 HOB/JC bans | blocked: no rule exists | still blocked, with a clear reason | Item 6 (D088, D093) |
| T3 NJ FAIR Act | empty: same date problem, and blocked on the T2 rules | **partial**: 140 NJ addresses, no flags | Flags need item 6 |
| T4 MA bills | blocked: only D089/D090 accepted, never fetched | **complete**: 110 MA addresses (if passed) | D045/D046 re-extracted (model call, no fetch) |
| T5 MA ballot | blocked: no failed record (D091) | **partial**: `[]` with a warning | Becomes complete once a failed record exists |
| Export and UI | 409 everywhere, page empty | T1, T3, T4, T5 exported; page shows all five with their status | — |

## Order and effort

| Step | Item | Effort | Needs DB / network / model? |
|---|---|---|---|
| 1 | Run each test separately, keep partial results, lenient export | ~2 h (backend + frontend) | no (tests use fixtures) |
| 2 | T2 counts `unknown` inside the right city | ~30 min | no |
| 3 | T5 without a failed record | ~30 min | no |
| 4 | T4 accepts the supplied status pages | ~1 h code + one extraction run | DB + `ANTHROPIC_API_KEY` |
| 5 | Calculated and second-document effective dates | ~3 h code + data run | DB; one fetch (D092) |

Do item 1 first: without it, none of the others can be seen. Items 2–4 are small and touch only `change_tracking/service.py`. Item 5 is the largest and the only one that changes Module B (`rule_adapter/review.py` and the shared `data/compiled_rules.json`), so coordinate it with whoever is working on Module B. Two people can split this: one takes 1, 2 and 3; the other takes 5 and the item 4 extraction.

---

## Item 1 — Run tests separately; degrade per test, not per export

**Problem.** `run_test` raises `ChangeInputError` for any missing or unverified input (`service.py:258-302`, `:436`, `:466`). Every route that runs the five tests (`/results`, `/stats`, `/export`, `/run`) runs them in one loop, so one blocked test returns a 409 for all five. The four dashboards and `/changes` then show nothing.

### Backend

**`schemas.py` — `ChangeTestResult`:** add
- `status: Literal["complete", "partial", "blocked"] = "complete"`
- `blocked_reason: str | None = None`
- `warnings: list[str] = []`

Keep `rules_resolved`.

**`service.py`:**
- Data problems produce a result instead of an exception. Add `_blocked(test, matches, reason, detail=None)`, which returns a `ChangeTestResult` with empty sets, the canonical matches already resolved, and `status="blocked"`. Use it for:
  - a missing primary canonical rule (except `negative` tests; see item 3)
  - a non-pending record in a `pending` test
  - one extracted rule mapped to two challenge IDs
  - a malformed test definition (`ValueError` at `:302`)
  - a failed T5 check (`:466`)
- `ChangeInputError` stays only for misuse: a partial run that asks to be saved (`:258`).
- **Missing `conflict_with` rules no longer block the test.** Compute the affected set, skip the flags, set `status="partial"`, and add a warning such as "Conflict check not run: JC-ALG-01, HOB-ALG-01 have no verified rule." Today `:267` treats missing conflict rules exactly like a missing primary rule.
- **Date preflight for `as_of` tests.** Before replaying, check the primary rules' compiled forms (`lookup_service.compiled_for`). If none has a usable effective date (`effective_date_unresolved`, or `not_yet_effective` with no dates), return blocked with a reason naming the rule and the fix: "Effective date of r-3321751a is unresolved; apply a date decision (data/date_decisions/)". This turns today's silent empty set into an actionable message.
- New `async def run_tests(session, tests, *, persist=False) -> list[ChangeTestResult]`. It is the single entry point for all routes and for the freeze script. Never save blocked results.

**`validation.py`:** split the checks into two kinds.

| Kind | Problems | Effect in lenient mode |
|---|---|---|
| **invalid** | not a list, unsorted or duplicate IDs, unknown address IDs, conflict IDs that are not in the affected set, missing notes, empty set outside T5, non-empty T5 | that test is left out of the export |
| **incomplete** | T3 without conflict flags; a test that is not among the five | kept in lenient mode, fatal in strict mode |

Add `check_changes(payload, address_ids) -> ChangeCheck(invalid, incomplete)`. Keep `validate_changes` as the strict, flattened list so the existing tests and callers keep working.

**`router.py`:**
- `/run`, `/results`, `/stats` call `run_tests` and always return 200 with per-test status. `/stats` counts only non-blocked results and adds `blocked: [test_id, ...]`.
- `/export` takes `strict: bool = Query(False)`:
  - **strict:** today's behaviour (409 unless all five are complete and valid).
  - **lenient:** leave out any blocked or invalid test, and set response headers `X-Changes-Complete: true|false` and `X-Changes-Omitted: T2,...`.
  - **Why omit rather than export `[]`:** the official template itself lists only some test IDs, so a missing key is a supported shape. An empty list would claim "no address is affected", and for a blocked T5 it would even score as correct while hiding a real failure.

**`scripts/freeze_submission.py`:** default stays strict. `--allow-incomplete` writes the lenient payload and prints which tests were left out and which are partial, next to the SHA-256 hashes.

### Frontend

The frontend and backend deploy separately: Vercel on every push, Fly only when `backend/` changes. So read `status` defensively, treat a missing `status` as `"complete"`, and ship both sides in the same push.

- `frontend/lib/types.ts`: add optional `status`, `blocked_reason`, `warnings` to `ChangeTestResult`, and optional `blocked` to `ChangeStats`.
- `frontend/app/changes/page.tsx`:
  - Keep the "Current change results are unavailable" notice only for an unreachable API.
  - Each case card shows a **Blocked** badge with `blocked_reason`, or a **Partial** badge with its warnings.
  - Never show "0 affected" for a blocked case.
- `frontend/components/explorer/changes-explorer.tsx` (around `:200`): a blocked case must not show "This case moves no building in the sample. That is a result, not a failure". Show the blocked reason instead.
- `frontend/components/home/{renter,provider,advocate,agency}.tsx`: filter out blocked results before any membership or count logic. Otherwise "your building is not affected" can rest on a test that never ran. Agency's table shows "blocked" in that test's row.

### Tests (`backend/tests/test_change_tracking.py`)

- With one canonical ID missing, `run_tests` returns five results and only that one is blocked. A lenient export leaves it out and sets the header; a strict export returns 409.
- T3 with conflict rules missing comes back `partial`, with its affected set and no flags.
- `check_changes` separates invalid from incomplete.
- The date preflight blocks a fixture whose rule date is unresolved.

**Done when:** `/changes` and the four dashboards render with whatever tests can run, and `GET /api/change-tracking/export` returns a file containing every non-blocked test.

---

## Item 2 — T2 counts `unknown` inside the right city

**Problem.** The boundary branch (`service.py:383-407`) counts only `_applies` (strict `applies`). 110 of the 115 compiled rules are still `needs_review` with unresolved coverage, so they evaluate to `unknown` at best. Even after the city rules are extracted, T2 would come out empty. T1 and T3 already count `unknown` as affected (`_LIVE`).

**Change.**
- Rename `_local_may_apply` (`:240`) to `_reaches(rule, address, day)`: the verified geography is `true` **and** the result is in `_LIVE`. Use it for both the T2 sets and the T3 conflict flags.
- An address whose legal city is unresolved has geography `unknown`, so it stays out. That matches "only inside its own city limits".
- `detail.per_rule[cid]` becomes `{"applies": [...], "coverage_unresolved": [...]}`. The notes report both counts. The affected set is their union.
- Frontend: `page.tsx` reads `per_rule` as `Record<string, string[]>`. Accept both shapes, or keep `per_rule` flat and add a sibling key `per_rule_split`. **Use the sibling key**; it is the smaller change.

**Tests:** a Hoboken rule that needs review (coverage unknown) reaches a verified Hoboken address, but not Jersey City, Newark, or a Hoboken-postal address whose legal city is unresolved.

---

## Item 3 — T5 without a failed record

**Problem.** `run_test` blocks T5 when no `MA-RENT-P1` record exists (`:267`, `:436`). But the expected answer is an empty set, and the check that actually guards it — no live rent cap at any MA address — runs and passes without that record.

**Change** (negative branch, `:434-486`):

| Situation | Result |
|---|---|
| Failed record present, status `failed` | `complete` (as today) |
| Failed record present, any other status | blocked: "requires a failed measure record" |
| **No record** | **`partial`**, warning: "No failed-measure record for MA-RENT-P1 has been captured (D091). The empty set rests on the rent-cap check alone: no Boston or Cambridge address has a live rent cap." `detail.failed_record = false` |
| Rent-cap check fails | blocked, with the offending addresses and rules in `detail` |

Remove `negative` tests' primary IDs from the generic missing check.

**Tests:** no record and no live cap gives `partial` with `[]`; no record with a live MA rent cap gives blocked; a record with status `in_force` gives blocked.

---

## Item 4 — T4 accepts the supplied bill status pages

**Problem.** `MA-ALG-P1` and `MA-ALG-P2` accept only D089/D090 (`service.py:55-70`), the operative bill texts, which have never been fetched. The supplied corpus already has each bill's official status page: **D046 = S.2983** ("An Act prohibiting algorithmic rent setting") and **D045 = H.5222**. Each gives the title and "Referred to … Ways and Means", which is enough for a `pending` record.

The counterfactual for this case only needs the bill's state geography. Coverage that stays `unknown` already counts as in scope (`_in_scope`). Quotes from the supplied corpus are also the safest for the "quoted span found in the corpus" citation score.

**Change.**
1. Selectors: `MA-ALG-P1 → ("D046", "D089")`, `MA-ALG-P2 → ("D045", "D090")`. Leave out D047 (S.2983's bill history) so P1 maps to one act through one page. The duplicate-mapping guard (`:283-288`) still keeps P1 and P2 apart.
2. **Check first:** the checked-in compiled store has *no* rules from D045, D046 or D047, so extraction most likely returned an empty list with a `document_note`. Confirm with `GET /api/rule-extraction/corpus/documents/D046` and `GET /api/rule-extraction/rules?doc_id=D046`.
3. If they are empty, add a clause to the Module A `SYSTEM_PROMPT` (`rule_extraction/service.py`), matching `docs/source_decision_tree.md:152`:
   > An official legislature bill page that has the bill's title and status but no operative text supports exactly one `pending` record for that bill: quote the title verbatim as `quoted_span`, write `requirement` from the title alone, leave coverage and exemptions empty, and set `confidence` no higher than 0.6.

   Bump `PROMPT_VERSION`. Then run `POST /api/rule-extraction/extract/D045` and `/extract/D046` **only**. Changing the version key re-bills only the documents you actually re-run.
4. Re-run Module B's compile step so the two new rules appear in `data/compiled_rules.json`, then commit it.

**Tests:** update `test_massachusetts_bill_ids_are_source_distinct`. With a fixture rule from D046 (S.2983) and one from D045, P1 and P2 resolve to different rules. A rule from D047 is not picked up.

**Done when:** `/canonical-rules` shows P1 → one D046 rule and P2 → one D045 rule, both `pending`, and T4 is `complete` with the 110 MA addresses. Each per-bill set is visible in `detail.per_rule`.

---

## Item 5 — Calculated and second-document effective dates (T1, T3)

**Problem, confirmed by compiling both rules.** The compiler accepts a date only if the date itself appears in the rule's own source with an "effective" cue nearby (`compiler.py:541-630`). The date review accepts a date only if a quoted passage *from that same document* contains it (`review.py:322-335`). Neither T1 nor T3 can pass that test:

| Test | Source | What the source says | What we need |
|---|---|---|---|
| **T1** | D022, AB 325 | "Approved by Governor October 06, 2025"; no effective date anywhere | 2026-01-01 |
| **T3** | D069, FAIR Act | "approved July 20, 2026" and "This act shall take effect on the first day of the twelfth month next following the date of enactment" | 2027-07-01 (calculated) |

The runbook's `apply_ab325_date` sets `Rule.effective_date` from D092. But the compiler then checks D022, not D092, so the date is still unresolved and the rule is still `unknown` on both dates. **The runbook's T1 step does not achieve T1 as written.**

### Design: one review mechanism, two new kinds of evidence

**5a — `enactment_offset` (same document; T3).** A date-review item can carry a `derivation` instead of a passage containing the literal date:

```json
{
  "raw": "2027-07-01",
  "derivation": {
    "kind": "enactment_offset",
    "enactment_span": "approved July 20, 2026",
    "rule_span": "This act shall take effect on the first day of the twelfth month next following the date of enactment."
  }
}
```

`review.py` verifies it in code before accepting it:
- Both passages occur in the rule's current source. Ignore whitespace differences when matching (D069 breaks that sentence across lines), but keep case.
- `enactment_span` contains one of approved/enacted/signed, plus exactly one date, parsed with `_SOURCE_DATE`.
- `rule_span` matches `first day of the (<ordinal>) month next following the date of enactment`. Accept ordinal words up to twelfth, or numbers like `12th`.
- The calculation is enactment month + N months, day 1. For T3: July 2026 + 12 = **2027-07-01**. It must equal `raw`; a reviewer cannot enter a date the calculation does not produce.

**5b — `external_document` (another captured corpus document; T1).**

```json
{
  "raw": "2026-01-01",
  "derivation": {
    "kind": "external_document",
    "doc_id": "D092",
    "source_hash": "<D092 content_hash at review time>",
    "span": "AB 325 (AGUIAR-CURRY), CH. 338 ... EFFECTIVE DATE: JANUARY 1, 2026",
    "act": {"bill": "AB 325", "chapter": "338"}
  }
}
```

Verified at review time, generalising `ab325_date_evidence`:
- The span occurs in the evidence document's stored body, and that body's hash equals `source_hash`.
- The span contains the date and **both** the bill number and the chapter.
- The rule's own source names the same act ("AB 325" and "CHAPTER 338" both appear in D022). This stops one bill's date being attached to another bill.

`apply_date_review` cannot reach the database. So the evidence document's hash is stored in the review record, and `freeze_submission.py` re-checks it against the current D092 body.

**Why not derive T1 from the California constitution's default start date?** That would encode a legal rule that is not in the corpus. Its "90-day period" wording is not something to reconstruct from memory. The brief forbids inventing rules where the source is silent, so T1 rests on a captured document that states the date.

### Decision files and the apply script

- **`data/date_decisions/*.json`** (checked in, one per act). Each holds a selector (`source_doc_id`, `category`, optional `citation_contains`), the `effective_date`, reviewer, rationale, and the `derivation`. The reasoning then lives in git, not only in a database row.
- **`scripts/apply_date_decisions.py`**, idempotent. For each decision:
  1. Select the rules, verify the evidence, and calculate or check the date.
  2. Set `Rule.effective_date`, with an audit record as `apply_ab325_date` does today. `rules.json` carries this field, and the extraction score checks it.
  3. Recompile. The rule's version hash changes with its date.
  4. Call `store.review_dates(...)` with the derivation, against the **new** hash, then `store.save()` and `clear_compiled_cache()`.
- Replace `apply_ca_effective_date.py` and `effective_date_review.apply_ab325_date` with this script. Point the two D022 hooks in `rule_extraction/service.py` (`:328-334`, `:552-557`) at a generic "re-apply decisions for this doc_id". A re-extraction then restores the date instead of quietly letting the review go stale. If the spans stopped verifying, item 1's date preflight blocks T1/T3 with the reason.

### Data run (needs DB and network)

1. `scripts/ingest_change_sources.py D092`: one official PDF.
2. Write the two decision files.
   - FAIR Act: all D069 `algorithmic_rent_setting` rules (in the checked-in store, `r-3321751a` and `r-b6484cfb`).
   - AB 325: the D022 `§16729` rules (`r-0f3287cc` and `r-b03a70bc`).
   - Select by source and category, not by these IDs.
3. Run `scripts/apply_date_decisions.py`, then commit `data/compiled_rules.json` and the decision files.

**Fallback if D092 cannot be captured or lacks the line:** add the official Business and Professions Code §16729 page as a new D094. It is on the same `leginfo.legislature.ca.gov` host and in the same format as D023–D027, which close with a statute history note such as "(… Stats. 2025, Ch. 340 … (AB 414) Effective January 1, 2026.)". Check the exact wording when you capture it.

### Tests

- Real D069 text (read from `corpus/text/`) with the decision above gives 2027-07-01. `evaluate_time` returns `not_yet_effective` on 2026-10-01 and in force on 2027-07-02.
- A wrong `raw` (2027-07-20), a passage that is not in the source, or an ordinal that doesn't parse are each rejected.
- External evidence with chapter 339, a mismatched hash, or a rule source that does not name AB 325 is rejected.
- A review recorded before the date was set (stale hash) does not apply.
- Replaying T1 and T3 on fixture addresses gives the transition, with T3 `partial` until the city rules exist.

---

## Verification after all five

1. `make test` and `make lint`.
2. In the environment holding the shared database:
   - `GET /api/change-tracking/results` shows T1 complete (~250), T2 blocked naming D088/D093, T3 partial (140, no flags), T4 complete (110), T5 partial (`[]`).
   - `GET /api/change-tracking/canonical-rules` shows MA-ALG-P1 and MA-ALG-P2 as distinct `pending` rules.
3. A lenient `GET /api/change-tracking/export` returns T1, T3, T4 and T5 with `X-Changes-Omitted: T2`. With `?strict=true` it returns 409 naming T2 and T3.
4. `scripts/freeze_submission.py --allow-incomplete` writes the three files and prints what was left out.
5. `/changes` in the browser:
   - each blocked or partial card states why;
   - one CA address shows its before/after rule set across 2025-12-31 and 2026-01-02;
   - no dashboard calls a building unaffected because of a blocked test.
6. Update [the runbook](module_c_runbook.md): replace step 3 (`apply_ca_effective_date.py`) with the date-decision script, add the D045/D046 re-extraction, and record the lenient and strict export modes.

## Follow-ups this plan deliberately leaves out

- **Item 6:** capture D088 and D093. This unblocks T2 and gives T3 its flags; with them T3 becomes complete.
- **T1 still reads as "unknown" in lookups.** After item 5, T1's affected set is right, but the AB 325 rule reads `unknown` at every CA address, because its coverage still needs review. The brief expects `applies` on 2026-01-02. A Module B coverage review would turn T1's `coverage_unresolved_after` into `covered_after` and fix `lookups.json`: approving it as `explicit_unconditional`, with a passage showing it is unconditional.
- **A sixth test breaks validation.** `REQUIRED_TEST_IDS` still requires exactly T1–T5, so a T6 trips it. Item 1 already turns an extra test into an "incomplete" warning instead of a fatal error, so this is partly addressed.
