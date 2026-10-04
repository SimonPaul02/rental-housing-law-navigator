# Reviewing Module B coverage

The compiler keeps a clause pending when it cannot verify the whole condition
against `Document.body`. An AI proposal is visible in the pending clause but
does not approve it. A human decision is stored in `data/compiled_rules.json`
and is valid only for the current rule hash, document-body hash, and compiler
version.

With the database running, inspect a rule:

```bash
cd backend
.venv/bin/python scripts/review_coverage.py --rule-id r-2b290690
```

The output shows each clause ID, the source passage, proposal, current
coverage and exemption trees, and the result after any existing review. Check
the document passage before writing a decision. A decision file supplies the
values printed by the inspection command:

```json
{
  "team_rule_id": "r-2b290690",
  "rule_version_hash": "<current rule hash>",
  "source_hash": "<current source hash>",
  "compiler_version": "3",
  "decision": "approve",
  "reviewer": "<reviewer name>",
  "rationale": "RSO membership is a material coverage condition in the cited passage.",
  "resolved_clause_ids": ["c1"],
  "coverage_basis": "conditions",
  "coverage": {
    "all": [{
      "id": "c1.1",
      "field": "los_angeles_rso_membership",
      "op": "is_true",
      "source_span": "<exact passage from Document.body>",
      "source_doc_id": "<source doc ID from inspection>",
      "source_hash": "<current source hash>",
      "clause_id": "c1",
      "origin": "coverage"
    }]
  }
}
```

Then run `.venv/bin/python scripts/review_coverage.py --decision decision.json`.
The review command validates the expression and source anchors before saving.
Submitting a later decision replaces the prior coverage decision for that rule;
include every clause you intend to keep resolved.
Use `"decision": "hold"` with a rationale when evidence is insufficient, or
`"decision": "reject"` when the current translation is wrong. Those decisions
leave coverage pending.
The RSO membership fact is absent from the supplied address dataset, so this
reviewed condition still yields `unknown` for Los Angeles addresses. Review
the non-RSO exemption separately; a decision resolving `c1` does not clear
other pending clauses.

For a genuinely unconditional rule, set `coverage_basis` to
`explicit_unconditional` with a rationale and list every cleared coverage
clause in `resolved_clause_ids`. Include `scope_evidence_span` with an exact
passage from the current `Document.body` that supports the scope finding.
The empty coverage tree remains unresolved
until that review is recorded.

## Date and relationship review (compiler v3)

Run `cd backend && .venv/bin/python scripts/compile_rules.py --no-model` after
Module A has populated the `Rule` and `Document.body` rows. Compiler v3
invalidates the old v1 snapshot and keeps ambiguous date meanings pending.
Use `scripts/replay_coverage_gate.py --as-of 2026-10-01` to rank high-impact
review cases. Its report places change-case rules first, then LA/SF rent rules,
then rules ordered by how many formerly applicable address pairs they affect.
Inspect every `new_applies` row before accepting the export.

Inspect a date with `scripts/review_dates.py --rule-id <id>`. A date decision
must include the current `rule_version_hash`, `source_hash`, and
`compiler_version` printed by inspection, plus a reviewer and rationale:

```json
{
  "team_rule_id": "<id>",
  "rule_version_hash": "<current hash>",
  "source_hash": "<current hash>",
  "compiler_version": "3",
  "reviewer": "<reviewer>",
  "rationale": "This passage describes the annual value period, not enactment.",
  "effective_dates": [],
  "key_value_period": {
    "start": "2025-07-01",
    "end": "2026-06-30",
    "source_span": "<exact passage in Document.body containing both dates>"
  }
}
```

Submit it with `scripts/review_dates.py --decision <file>`. An empty
`effective_dates` list is an explicit finding that the record has no
rule-effective date. When clearing an ambiguous date without a value period,
include `role_evidence_span` with the exact source passage that explains the
date's other role. For a genuine effective date, include an entry with
`raw` and its exact `source_span`. The script rejects stale versions and dates
absent from the passage. A value outside its reviewed period is hidden from
the address API, while the rule may still apply.

Run `scripts/review_rules.py` to inspect current relationship candidates.
There is no automatic approval. A relationship decision file supplies
`left_rule_id`, `right_rule_id`, `issue_key`, `reviewer`, `rationale`,
`qualification` (`confirmed`, `excluded`, or `unresolved`), and exact
`left_evidence_span` and `right_evidence_span` from the two source documents.
It may add `valid_from` when the qualification starts on a known date. Submit
with `scripts/review_rules.py --decision <file>`. Changed rule wording or
source bodies invalidate the decision. Until a plausible displacement is
confirmed, an affected state rule is `unknown` with a named precedence
question; a definitely excluded relationship leaves the base result intact.

The standalone lookup export now compares database IDs with the 500 distinct
IDs in `data/sample_addresses.csv` before evaluating rules. A partial import
cannot produce a passing `lookups.json`.
