"""Module A's two code-side guards: span verification and rule identity.

These cover the live extraction path in `service.py`, which is the code that
decides what reaches rules.json - as distinct from `test_citations.py` and
`test_merge.py`, which cover pipeline stages that path does not yet call.
"""

from __future__ import annotations

import asyncio
import glob
import os

import pytest

from app.modules.rule_extraction.pipeline.text import unwrap, wrap_width
from app.modules.rule_extraction.schemas import (
    Category,
    ExtractedRule,
    Level,
    RuleRecord,
    RuleStatus,
)
from app.modules.rule_extraction.service import (
    _assign_ids,
    _normalise,
    span_occurs_in,
    stable_rule_id,
)

CORPUS = sorted(
    glob.glob(os.path.join(os.path.dirname(__file__), "..", "..", "corpus", "text", "*.txt"))
)


def rule(**overrides) -> ExtractedRule:
    base = {
        "jurisdiction": "NJ",
        "level": Level.state,
        "category": Category.screening_restrictions,
        "status": RuleStatus.in_force,
        "title": "Fair Chance in Housing Act",
        "requirement": "No criminal record inquiry before a conditional offer.",
        "citation": "N.J.S.A. 46:8-55",
        "quoted_span": "A housing provider shall not make any oral or written inquiry.",
        "confidence": 0.9,
    }
    base.update(overrides)
    return ExtractedRule.model_validate(base)


# -- span verification ------------------------------------------------------
def test_span_matches_across_a_line_break():
    """The whole reason the check normalises: the source is hard-wrapped."""
    body = "A housing provider\nshall not make any oral or written\ninquiry regarding records."
    assert span_occurs_in("A housing provider shall not make any oral or written inquiry", body)


def test_span_matches_ignoring_case_and_double_spaces():
    assert span_occurs_in("SHALL  NOT   MAKE", "the provider shall not make an inquiry")


def test_span_absent_from_source_is_rejected():
    assert not span_occurs_in(
        "shall cap rent at 3 percent", "the provider shall not make an inquiry"
    )


@pytest.mark.parametrize("span,body", [("", "something"), ("something", ""), ("", "")])
def test_empty_span_or_body_is_rejected(span, body):
    assert not span_occurs_in(span, body)


# -- unwrapping -------------------------------------------------------------
WRAPPED = "\n".join(
    [
        "a. A housing provider shall not require an applicant to submit to a drug",
        "or alcohol test, or request the applicant's consent to obtain information",
        "from a treatment facility.",
        "",
        "b. Prior to accepting any application fee, a housing provider shall",
        "disclose in writing to the applicant the provider's screening criteria.",
    ]
    + [f"line {n} padding to establish the wrap margin for this sample text ok" for n in range(30)]
)


def test_wrapped_lines_become_whole_sentences():
    out = unwrap(WRAPPED)
    assert "submit to a drug or alcohol test, or request the applicant's consent" in out


def test_paragraph_break_survives_unwrapping():
    out = unwrap(WRAPPED).split("\n")
    assert "" in out, "a blank line is a deliberate break and must be kept"


def test_short_mid_sentence_fragments_are_joined():
    """The case the margin test alone misses - PDF text also emits stubs."""
    text = "\n".join(
        [
            "(2)",
            "A housing",
            "provider",
            "shall not make any oral or written inquiry",
            "regarding a record.",
        ]
        + [f"filler line {n} long enough to set a believable right margin here" for n in range(30)]
    )
    out = unwrap(text)
    assert (
        "(2) A housing provider shall not make any oral or written inquiry regarding a record."
        in out
    )


def test_a_new_subsection_is_never_joined_to_the_line_above():
    text = "\n".join(
        [
            "the applicant may provide evidence of rehabilitation or other factors",
            "(3) The housing provider shall perform an individualized assessment.",
        ]
        + [f"filler line {n} long enough to set a believable right margin here" for n in range(30)]
    )
    assert "factors (3)" not in unwrap(text)


def test_paragraph_per_line_text_is_left_alone():
    text = "\n".join(f"Paragraph {n}. " + "word " * 80 for n in range(30))
    assert unwrap(text) == text


def test_too_few_lines_to_judge_is_left_alone():
    text = "short\nlines\nonly\nhere"
    assert wrap_width(text.split("\n")) is None
    assert unwrap(text) == text


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: os.path.basename(p))
def test_unwrapping_cannot_break_span_verification(path):
    """The invariant the whole change rests on.

    Unwrapping only ever replaces a newline with a space, and the span check
    normalises whitespace - so a span the model quotes from the unwrapped text
    still verifies against the stored original. If this ever fails, verified
    spans start getting discarded.
    """
    raw = open(path, encoding="utf-8").read()
    assert _normalise(unwrap(raw)) == _normalise(raw)


def test_some_of_the_corpus_is_actually_wrapped():
    """Guards the thresholds: if a tweak makes them match nothing, say so."""
    touched = sum(
        1
        for p in CORPUS
        if unwrap(open(p, encoding="utf-8").read()) != open(p, encoding="utf-8").read()
    )
    assert touched > 0, "the unwrapper no longer fires on any supplied document"


# -- rule identity ----------------------------------------------------------
def test_rule_id_is_stable_across_runs():
    args = ("D065", "NJ", "screening_restrictions", "N.J.S.A. 46:8-55", 0)
    assert stable_rule_id(*args) == stable_rule_id(*args)


def test_rule_id_ignores_formatting_differences():
    a = stable_rule_id("D065", "NJ", "screening_restrictions", "N.J.S.A. 46:8-55", 0)
    b = stable_rule_id(" d065 ", " nj ", "SCREENING_RESTRICTIONS", " N.J.S.A. 46:8-55 ", 0)
    assert a == b


def test_rule_id_differs_per_document_citation_and_ordinal():
    base = ("D065", "NJ", "screening_restrictions", "N.J.S.A. 46:8-55", 0)
    variants = {
        stable_rule_id(*base),
        stable_rule_id("D066", *base[1:]),
        stable_rule_id(*base[:3], "N.J.S.A. 46:8-56", 0),
        stable_rule_id(*base[:4], 1),
    }
    assert len(variants) == 4


def test_finding_an_extra_rule_does_not_renumber_the_others():
    """The defect this replaced: a counter reassigned every id on re-extraction,
    silently invalidating the team_rule_id references in lookups.json."""
    first = _assign_ids("D065", [rule()])
    later = _assign_ids(
        "D065",
        [
            rule(citation="N.J.S.A. 46:8-60", title="Drug testing", quoted_span="x" * 30),
            rule(),
        ],
    )
    assert set(first.values()).issubset(set(later.values()))


def test_ids_do_not_depend_on_the_order_the_model_returned_them():
    a = rule(title="Aardvark", quoted_span="a" * 30)
    b = rule(title="Zebra", quoted_span="z" * 30)
    forward = _assign_ids("D065", [a, b])
    backward = _assign_ids("D065", [b, a])
    assert {forward[0], forward[1]} == {backward[0], backward[1]}
    assert forward[0] == backward[1], "the same obligation keeps the same id"


def test_two_obligations_under_one_citation_get_distinct_ids():
    ids = _assign_ids("D001", [rule(title="Selling", quoted_span="s" * 30), rule(title="Using")])
    assert len(set(ids.values())) == 2


# -- concurrent progress ----------------------------------------------------
class _CapturingSession:
    """Records the statement instead of running it, as the other tests do."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)


async def _progress_sql() -> str:
    from sqlalchemy.dialects import postgresql

    from app.modules.rule_extraction.service import _record_progress

    session = _CapturingSession()
    await _record_progress(session, "run-1", {"doc_id": "D065"}, docs_done=1, rules_extracted=5)
    return str(
        session.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "extraction_runs.docs_done + ",
        "extraction_runs.rules_extracted + ",
        "extraction_runs.events || ",
    ],
)
def test_progress_arithmetic_happens_in_the_database(fragment):
    """Counters must be incremented by Postgres, not in Python.

    `run.docs_done += 1` on an ORM row reads, adds and writes back, so two of
    the concurrent per-document sessions overwrite each other. A 54-document
    pass at concurrency 4 lost 39 increments and 39 SSE progress events while
    reporting 0 failures.
    """
    sql = asyncio.run(_progress_sql()).replace("\n", " ")
    assert fragment in sql, sql


# -- importing a pass run elsewhere -----------------------------------------
class _ImportSession:
    """Stands in for the database, in the style of test_address_integration."""

    def __init__(self, bodies: dict[str, str], existing: dict | None = None) -> None:
        self.bodies = bodies
        self.existing = existing or {}
        self.added: list = []
        self.deleted = 0

    async def execute(self, statement):
        sql = str(statement).strip().upper()

        class _Result:
            def __init__(self, rows=None, count=None, rowcount=0):
                self._rows, self._count, self.rowcount = rows or [], count, rowcount

            def all(self):
                return self._rows

            def scalar_one(self):
                return self._count

        if sql.startswith("DELETE"):
            self.deleted = len(self.existing)
            self.existing = {}
            return _Result(rowcount=self.deleted)
        if "COUNT" in sql:
            return _Result(count=len(self.added) + len(self.existing))
        return _Result(rows=list(self.bodies.items()))

    async def get(self, _model, key):
        return self.existing.get(key)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _record(**overrides) -> RuleRecord:
    base = {
        "team_rule_id": "r-abc12345",
        "jurisdiction": "NJ",
        "level": "state",
        "category": "screening_restrictions",
        "status": "in_force",
        "title": "Fair Chance in Housing Act",
        "requirement": "No inquiry before a conditional offer.",
        "citation": "N.J.S.A. 46:8-55",
        "source_doc_id": "D065",
        "source_url": "https://example.gov/D065",
        "quoted_span": "A housing provider shall not make any oral or written inquiry.",
        "confidence": 0.9,
    }
    base.update(overrides)
    return RuleRecord.model_validate(base)


BODY = "Text. A housing provider\nshall not make any oral or written\ninquiry. More text."


def _import(records, *, replace=False, bodies=None, existing=None):
    from app.modules.rule_extraction.service import import_rules

    session = _ImportSession(bodies if bodies is not None else {"D065": BODY}, existing)
    return asyncio.run(import_rules(session, records, replace=replace)), session


def test_import_accepts_a_record_whose_span_is_in_this_deployments_document():
    result, session = _import([_record()])
    assert (result.inserted, result.updated, result.rejected) == (1, 0, [])
    assert len(session.added) == 1


def test_import_re_verifies_the_span_and_rejects_a_tampered_record():
    """The guard is re-run rather than trusted, so an import cannot introduce
    a rule this deployment's corpus does not support."""
    result, session = _import([_record(quoted_span="landlords may raise rent by 40 percent")])
    assert result.inserted == 0
    assert session.added == []
    assert "does not occur" in result.rejected[0].reason


def test_import_rejects_a_record_for_a_document_this_deployment_lacks():
    result, _ = _import([_record(source_doc_id="D999")], bodies={})
    assert result.inserted == 0
    assert "unknown document D999" in result.rejected[0].reason


def test_import_with_replace_clears_the_existing_rules_first():
    result, _ = _import(
        [_record()], replace=True, existing={"r-0001": object(), "r-0002": object()}
    )
    assert result.deleted == 2
    assert result.inserted == 1


def test_importing_the_same_pass_twice_updates_instead_of_duplicating():
    """Content-addressed ids make an import idempotent."""

    class _Row:
        pass

    row = _Row()
    result, session = _import([_record()], existing={"r-abc12345": row})
    assert (result.inserted, result.updated) == (0, 1)
    assert session.added == []
    assert row.quoted_span == _record().quoted_span
