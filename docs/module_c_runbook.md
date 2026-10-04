# Module C runbook

This runbook completes the [Module C plan](module_c_implementation_plan.md) after the project database and public source hosts are reachable. The code is deliberately fail closed: a blocked source or missing extracted rule prevents a complete `changes.json` export.

## Source roster

The additional link-only records in [`corpus_manifest.csv`](../corpus/corpus_manifest.csv) are bounded to named public URLs:

| ID | Purpose | Source |
|---|---|---|
| D088 | Jersey City §218-12 ordinance | [City ordinance PDF](https://www.jerseycitynj.gov/common/pages/GetFile.ashx?key=3949AR0g) |
| D089 | S.2983 operative bill text | [Massachusetts legislature](https://malegislature.gov/Bills/194/S2983.Html) |
| D090 | H.5222 operative bill text | [Massachusetts legislature](https://malegislature.gov/Bills/194/H5222/House/Bill/Text) |
| D091 | IP 25-21 disposition | [Cella v. Attorney General court opinion mirror](https://law.justia.com/cases/massachusetts/supreme-court/2026/sjc-13893.html) — secondary source pending an official opinion capture |
| D092 | AB 325 date evidence | [California Courts 2025 legislation summary](https://courts.ca.gov/system/files/file/2025-summary-final.pdf) |
| D093 | Hoboken adoption notice | [City of Hoboken](https://www.hobokennj.gov/news/city-of-hoboken-outlaws-algorithmic-rent-fixing) |

D034 is the Hoboken code publisher's §158-2 page. Its manifest row requires a terms check, and the current automated run does not fetch it. If a lawful official copy becomes available, use that as the primary source and review the canonical selector. D093's official city notice supplies the current bounded path.

## Run in a network-enabled environment

1. Set the backend database and extraction-model environment variables, migrate, and seed the manifest: `make migrate && make seed`. Re-seeding preserves already fetched bodies.
2. From `backend/`, run `.venv/bin/python scripts/ingest_change_sources.py --extract`. The script only requests the six rostered URLs, obeys robots decisions and pacing, stores source bodies and hashes in the database, records gaps, and invokes Module A's span-verified extractor. Unchanged source text reuses the extraction cache. D092 is date evidence only and is not sent to rule extraction.
3. Run `.venv/bin/python scripts/apply_ca_effective_date.py`. This requires the AB 325 Chapter 338 date span in captured D092 and verified §16729 rules from D022. A later D022 extraction or import reapplies the correction when D092 is present.
4. Review `/api/change-tracking/canonical-rules` for all seven challenge IDs. S.2983 and H.5222 must map to different source IDs. Review the fetched quotes, statuses, effective dates, and compiled coverage. Re-run Module B's compilation/review workflow if a rule version changed.
5. Run `make test` and `make lint`. Then run `POST /api/change-tracking/run` with `{"persist": true}` for a saved audit view. `GET /api/change-tracking/results` and `/export` always recompute from current inputs, so saved rows cannot be mistaken for a current answer. Each case reports its own `status` (`complete`, `partial`, or `blocked` with a `blocked_reason`), so one missing source blocks only the case that needs it.
6. From `backend/`, run `.venv/bin/python scripts/freeze_submission.py`. It validates all 500 addresses, every quoted span, the Module B lookup export, all five Module C cases, and the T3/T5 guardrails **before** writing `rules.json`, `lookups.json`, and `changes.json` at the repository root. Record the printed SHA-256 hashes with the submission. If some cases are still blocked, `--allow-incomplete` freezes the answered ones: blocked cases are left out of `changes.json` (never written as an empty list) and listed in the output.

If any source is blocked or extraction does not produce the required record, inspect the corresponding `coverage_gaps` or extraction audit entry and use a permitted primary source. Do not turn a missing record into an empty result. The T5 court mirror should be replaced with an official opinion when one can be captured; retain both source versions in the audit trail.

The current Codex sandbox cannot connect to the configured database or public hosts directly. The local code and unit tests can be verified here; the full data replay and frozen submission files require the network-enabled project environment.
