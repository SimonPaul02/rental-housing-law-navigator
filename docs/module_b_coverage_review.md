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
  "compiler_version": "2",
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
clause in `resolved_clause_ids`. The empty coverage tree remains unresolved
until that review is recorded.
