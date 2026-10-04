# Module B source-backed replay, 2026-10-01

The current Supabase database contains 115 `Rule` rows, 93 `Document` rows,
and all 500 sample addresses. Compiler v3 rebuilt every rule against the
current `Document.body`. The older compiler-v1 coverage and 35 phrase-based
relationship approvals are not used.

The first conservative rebuild translated no building conditions: most Module
A coverage clauses paraphrase the document rather than quote a passage that
states a complete checkable condition. It turned 5,186 formerly applicable
address-rule pairs into `unknown`, with no newly applicable pairs.

Targeted, source- and version-bound reviews recorded under Paul:

- `r-52dbca2e`, `r-96f16208`, and `r-a2190629`: the cited California
  screening-fee duties have actor and transaction conditions but no additional
  building coverage condition in their source passages. Their 750 former
  `applies` pairs were restored.
- `r-027144eb`: LA RSO membership is an explicit condition, not something
  implied by a Los Angeles address. Its utility-surcharge prohibition begins
  on 2026-02-02, as stated in the LAHD passage. Membership remains unknown
  for the supplied addresses.
- `r-28dd2ea6`: SF Rent Ordinance membership is an explicit condition, not
  something implied by a San Francisco address. The sample supplies no
  ordinance-membership fact.
- The CA state just-cause relationship to `r-28dd2ea6` and `r-71283b33` was
  recorded as unresolved. The current SF and LA source bodies do not establish
  the state-law adoption cutoff or later protective finding. Neither
  relationship is approved to supersede the state rule.

The final replay in [the ranked CSV](../data/coverage_v2_replay.csv) covers
500 addresses × 115 rules. It has **0 new `applies`**, **750 restored
`applies`**, and **4,436 formerly applicable pairs still `unknown`**. Every
remaining `unknown` row has a named pending clause, date-role review,
unresolved coverage question, or missing address fact in its explanation.
There are 418 pending clauses across 106 rules, 31 date reviews needed, and
35 unapproved relationship candidates. This is an intentional conservative
queue, not a claim that all laws have been resolved.

The standalone lookup export validates with exactly 500 address keys, 750
`applies`, 13,607 `unknown`, 106 `pending`, and no validation problems. The
non-writing submission freeze preflight stops at `T2`: the current Module A
database has no verified `HOB-ALG-01` or `JC-ALG-01` rules. None of the seven
rule IDs named in `dev/change_tests.json` are present in the current Module A
rows, so the change-case ranking cannot assign those IDs to live rules yet.
No development scoring key or scoring script is present in this checkout.
