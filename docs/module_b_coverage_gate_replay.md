# Module B coverage gate replay

Date: 2026-10-04. Address set: `data/sample_addresses.csv` joined to
`data/resolved_addresses.json` (500 addresses). Rule set: the 115 checked-in
compiler v1 revisions. Evaluation date: 2026-10-04.

This is a historical v1 quarantine report. The subsequent compiler v3 work
separates rule-effective dates from annual value periods and invalidates the
legacy relationship approvals. Its live rebuild and replay still require the
Module A database; see `docs/module_b_coverage_review.md` for the current
commands and review formats.

## What was available

The local Postgres service on port 5432 was unavailable. The current Module A
`Rule` rows and `Document.body` values therefore could not be loaded. A true
compiler v2 rebuild and source-verified replay is pending. Run
`cd backend && .venv/bin/python scripts/compile_rules.py --no-model` once the
database is available, then run
`cd backend && .venv/bin/python scripts/replay_coverage_gate.py` to compare
the rebuilt rules with the v1 baseline and export every relevant decision.

The checked-in v1 artifact has been **quarantined**. All 115 revisions are
marked `needs_review`, have `coverage_basis: unresolved`, and carry a legacy
verification marker. Their old condition trees were removed from the active
fields because they have not been verified against `Document.body`. The
runtime rejects them by compiler version and recompiles from current rules
and source documents when the database becomes available. The legacy
expressions remain recoverable from Git history. This removed 13 nonempty
coverage trees and 27 nonempty exemption trees from the active snapshot;
their expressions must be rederived or human reviewed during the v2 rebuild.
The 155 old AI clearance notes are labeled as unreviewed suggestions.
`data/coverage_expression_changes.csv` lists each of the 33 revisions whose
active expression tree changed during quarantine.

## Safety quarantine comparison

This comparison tests the effect of quarantining the old snapshot. It is **not**
the final compiler v2 replay. The baseline simulates the previous evaluator's
empty-coverage approval behavior. The after side uses the quarantined artifact.

| Result | Before | After quarantine |
| --- | ---: | ---: |
| `applies` | 5,186 | 0 |
| `unknown` | 8,619 | 14,077 |
| `does_not_apply` | 43,068 | 43,037 |
| `superseded` | 241 | 0 |
| `not_yet_effective` | 280 | 280 |
| `pending` | 106 | 106 |

The comparison covers 57,500 address-rule pairs. It recorded 5,458 changed
results: 5,186 `applies` → `unknown`, 241 `superseded` → `unknown`, and 31
`does_not_apply` → `unknown`. No new `applies` result was produced. Every
changed pair is in `data/coverage_quarantine_replay.csv`, so the 5,186 former
`applies` decisions can be inspected individually after the real rebuild.

Before quarantine, 55 revisions were labeled `approved`, including 53 with an
empty coverage tree and no recorded human coverage review. The Los Angeles
RSO rule `r-2b290690` changed from `applies` to `unknown` for 78 sampled
addresses. Its old notes had treated “Rental units subject to the City of Los
Angeles Rent Stabilization Ordinance (RSO)” and “Non-RSO properties are not
covered” as adding no building condition. Compiler v2 now keeps those clauses
pending unless their meaning is independently verified or a human records a
version-bound decision.
The review queue places that RSO rule first, followed by the other 52 former
automatic approvals with empty coverage.

## Remaining acceptance work

1. Rebuild from current `Rule` and `Document.body` records with compiler v2.
2. Run `scripts/replay_coverage_gate.py` against all 500 addresses and inspect
   every `new_applies` and `former_applies_unknown` row in its CSV output.
3. Review the 53 previously approved empty-coverage rules first, beginning
   with `r-2b290690`, using `scripts/review_coverage.py` and the operator
   example in `docs/module_b_coverage_review.md`.

The source or meaning of a clause that cannot be verified remains pending,
and its affected lookup remains `unknown`.
