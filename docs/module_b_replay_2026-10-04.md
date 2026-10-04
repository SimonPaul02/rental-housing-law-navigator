# Module B replay, 2026-10-04

Fix-forward from commit 90ece39. Measured two ways that agree exactly: the
service's own `run_lookup_export` against the shared database (read-only), and
`backend/scripts/replay_offline.py` against `data/rule_snapshot.json` (115 rules,
38 source bodies, taken from the same database the same day). Query date
2026-10-01, 500 sample addresses.

## Results

| Result | Before (90ece39) | After | of which high confidence |
|---|---:|---:|---:|
| `applies` | 750 | 9,807 | 750 |
| `superseded` | 0 | 422 | 0 |
| `not_yet_effective` | 0 | 280 | 0 |
| `pending` | 106 | 106 | 0 |
| `unknown` | 13,607 | 3,168 | 1,379 |
| addresses with nothing that applies | 250 (every NJ and MA address) | 0 | |

`lookups.json` validates: 500 addresses, four fields per item, no problems.
Confidence is not one of the four fields; a low answer says
"Confidence: low, for human review - ..." in its explanation.

High confidence means every check behind the answer is the cited source or a
named reviewer's decision. The 750 high-confidence answers are the three
human-reviewed CA screening-fee rules. Everything the machine read is low
confidence and stays in the review queue: 83 rules are `machine_classified`,
27 still `needs_review`, 5 `human_approved` (all five reviews still bound).

## The brief's example

San Francisco, 1110 Jackson St (A0236), built 1961, 9 units:

| Rule | Result |
|---|---|
| SF annual allowable increase (r-c0776898) | applies - built before the 1979-06-13 cutoff year |
| AB 1482 cap (r-0d968a45), statewide cap (r-492dc4cf) | superseded by r-c0776898 |
| SF Rent Ordinance just cause (r-28dd2ea6) | applies |
| CA just cause §1946.2 (r-48c8c1b0) | superseded by r-28dd2ea6, with the reviewer's hold note |
| CA deposit cap (r-1c1f3af9) | applies - the small-landlord exemption needs at most 4 units |
| AB 325, SF §37.10C | apply |

A Los Angeles building from 1978 stays unknown under both the RSO and AB 1482.
One from 2012 is outside the RSO and, by the fifteen-year rule, outside AB 1482
and CA just cause; the LA Just Cause Ordinance applies.

## Change cases (Module C, live)

| Case | Status | What remains |
|---|---|---|
| T1 AB 325 | blocked | D092 is not captured, so the D022 rules carry no date. Capture D092, then `scripts/apply_ca_effective_date.py` (database write) records a cross-document date review. |
| T2 Hoboken / Jersey City | blocked | No extracted HOB/JC rules (D088, D093 link-only). |
| T3 FAIR Act | partial: 140 NJ addresses | Date computed from D069's own enactment clause: not yet effective on 2026-10-01, applies on 2027-07-02. Conflict flags need the HOB/JC rules. |
| T4 MA bills | blocked | No pending MA rules extracted (D089, D090). |
| T5 MA ballot | partial: empty set | Guardrail holds; c.40P (D048), the state bar on local rent control, reports applies. |

## What is still unknown, and why

The largest remaining groups, each named in its explanation:

- exemptions only an owner's identity settles - a tenant sharing a kitchen or
  bath with the owner, a religious organization's housing - in CA just cause,
  Berkeley, San Diego and the NJ Law Against Discrimination;
- Berkeley's rent-ceiling rules: coverage turns on the year built, which the
  Berkeley rows do not have;
- coverage borrowed from another provision (San Diego's "subject to the
  Division", the relocation rules of §1946.2) and contingencies (MA's "only if
  and when regulations are promulgated");
- dates the source does not state: 14 rules, read at low confidence;
- 23 rules with an unresolved clause in all.
