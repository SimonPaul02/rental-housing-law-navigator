"""Module A: turn corpus documents into structured rule records.

Extraction is automated end to end - the model reads the supplied document
text and returns schema-shaped records. Nothing here hand-codes a rule.

Two guards matter for scoring:
  1. Every rule must carry a `quoted_span` that really occurs in the source
     document. We verify that in code and drop rules that fail, so a
     hallucinated citation can never reach the submission.
  2. team_rule_id is assigned by us, not the model, so ids stay unique.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import get_client
from app.db.models import Document, ExtractionRun, Rule
from app.modules.rule_extraction.schemas import (
    DocumentExtraction,
    ExtractDocResult,
    RuleRecord,
)

log = logging.getLogger(__name__)

CATEGORIES = [
    "rent_increase_limits",
    "just_cause_eviction",
    "security_deposits",
    "application_screening_fees",
    "screening_restrictions",
    "algorithmic_rent_setting",
]

SYSTEM_PROMPT = f"""\
You extract structured rule records from United States residential rental \
housing law. You are given the full text of one official document (a statute, \
ordinance, regulation, ballot measure or bill) and you return every in-scope \
rule it contains.

The query date is {settings.default_as_of}. Judge `status` as of that date.

## In-scope categories

Only these six categories count. Ignore everything else in the document.

- `rent_increase_limits` - caps or formulas limiting how much rent may rise.
- `just_cause_eviction` - restrictions on the grounds for terminating a tenancy.
- `security_deposits` - limits on deposit amount, holding, interest or return.
- `application_screening_fees` - caps on what an applicant may be charged.
- `screening_restrictions` - limits on using criminal, credit, income or \
source-of-income information to screen applicants.
- `algorithmic_rent_setting` - restrictions on coordinated or algorithmic \
pricing software used to set rents or manage occupancy.

## status

- `in_force` - enacted and effective on or before the query date.
- `not_yet_effective` - enacted, but its effective date is after the query date.
- `pending` - still a bill or measure; not enacted.
- `failed` - defeated, struck, repealed or rejected.

## Rules for every record

- `jurisdiction`: a two-letter state code for state law (`CA`, `NJ`, `MA`), or \
`City, ST` for local law (`San Francisco, CA`). Match `level` accordingly.
- `quoted_span`: copy the exact supporting sentence VERBATIM from the document. \
Do not paraphrase, normalise, fix typos or join distant sentences. It is \
checked character-for-character against the source and the record is discarded \
if it does not match. Prefer one clean sentence that carries the obligation.
- `coverage_conditions`: state cutoffs precisely, including whether a cutoff is \
on a year built or a certificate-of-occupancy date, and the exact date. These \
are evaluated against real buildings, so "older buildings" is useless and \
"certificate of occupancy issued on or before 1979-06-13" is correct.
- `key_value`: the headline number or formula, if the rule has one.
- `requirement`: one or two plain-language sentences a tenant could read.
- `confidence`: your genuine confidence the record is correct and supported.
- One record per distinct obligation. If a single section caps deposits AND \
requires interest, that is two records only if they are separately actionable; \
otherwise keep one with the detail in `requirement`.
- If the document contains no in-scope rule, return an empty `rules` list and \
say why in `document_note`. An empty list is a correct answer for a document \
that is only procedural, a notice, or out of scope.

Do not invent rules that the document does not state. Do not carry over \
knowledge of a law that is not in this document's text.
"""


def _normalise(text: str) -> str:
    """Collapse whitespace so span matching survives PDF-to-text artefacts."""
    return re.sub(r"\s+", " ", text).strip().lower()


def span_occurs_in(span: str, body: str) -> bool:
    """True if `span` appears in `body`, ignoring whitespace and case.

    The corpus is PDF-extracted text full of ragged line breaks and double
    spaces, so an exact `in` test would reject almost every real quote.
    """
    if not span or not body:
        return False
    return _normalise(span) in _normalise(body)


async def _next_rule_index(session: AsyncSession) -> int:
    """Highest existing r-NNNN index, so ids never collide across runs."""
    result = await session.execute(select(Rule.team_rule_id))
    highest = 0
    for (rid,) in result:
        m = re.fullmatch(r"r-(\d+)", rid or "")
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


async def extract_document(
    session: AsyncSession,
    doc: Document,
    *,
    run_id: str | None = None,
    replace: bool = True,
) -> ExtractDocResult:
    """Run the model over one document and persist the rules it supports."""
    if not doc.body:
        raise ValueError(
            f"{doc.doc_id} has no supplied text (capture={doc.capture!r}); "
            "link-only sources cannot be extracted."
        )

    client = get_client()
    response = await client.messages.parse(
        model=settings.extraction_model,
        max_tokens=settings.extraction_max_tokens,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Stable across all 54 documents - cache it once per run.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f'<document doc_id="{doc.doc_id}" '
                    f'jurisdictions="{doc.jurisdictions}" '
                    f'source_url="{doc.url}">\n'
                    f"{doc.body}\n"
                    f"</document>\n\n"
                    "Extract every in-scope rule record from this document."
                ),
            }
        ],
        output_format=DocumentExtraction,
    )

    parsed: DocumentExtraction = response.parsed_output
    usage_in = getattr(response.usage, "input_tokens", 0) or 0
    usage_out = getattr(response.usage, "output_tokens", 0) or 0

    if replace:
        await session.execute(delete(Rule).where(Rule.source_doc_id == doc.doc_id))
        await session.flush()

    next_idx = await _next_rule_index(session)
    kept: list[RuleRecord] = []
    rejected = 0

    for extracted in parsed.rules:
        if not span_occurs_in(extracted.quoted_span, doc.body):
            rejected += 1
            log.warning(
                "dropping rule from %s: quoted_span not found in source (%r)",
                doc.doc_id,
                extracted.quoted_span[:80],
            )
            continue

        team_rule_id = f"r-{next_idx:04d}"
        next_idx += 1

        rule = Rule(
            team_rule_id=team_rule_id,
            jurisdiction=extracted.jurisdiction,
            level=extracted.level.value,
            category=extracted.category.value,
            status=extracted.status.value,
            title=extracted.title,
            requirement=extracted.requirement,
            key_value=extracted.key_value,
            coverage_conditions=extracted.coverage_conditions,
            exemptions=extracted.exemptions,
            overrides=[],
            interaction=extracted.interaction,
            effective_date=extracted.effective_date,
            citation=extracted.citation,
            source_doc_id=doc.doc_id,
            source_url=doc.url,
            quoted_span=extracted.quoted_span,
            confidence=extracted.confidence,
            conflict_flag=False,
            conflict_note=None,
            run_id=run_id,
        )
        session.add(rule)
        kept.append(RuleRecord.model_validate(rule))

    await session.flush()

    return ExtractDocResult(
        doc_id=doc.doc_id,
        rules=kept,
        document_note=parsed.document_note,
        spans_verified=len(kept),
        spans_rejected=rejected,
        input_tokens=usage_in,
        output_tokens=usage_out,
    )


async def start_run(session: AsyncSession, doc_ids: list[str], model: str) -> ExtractionRun:
    run = ExtractionRun(
        run_id=str(uuid.uuid4()),
        status="running",
        model=model,
        doc_ids=doc_ids,
        docs_total=len(doc_ids),
        started_at=dt.datetime.now(dt.UTC),
        events=[],
    )
    session.add(run)
    await session.flush()
    return run


async def run_extraction(run_id: str, doc_ids: list[str], replace: bool) -> None:
    """Background corpus pass. Each document gets its own session and commit,
    so progress survives a failure partway through and the SSE endpoint can
    watch the row advance."""
    from app.core.db import SessionLocal

    sem = asyncio.Semaphore(settings.extraction_concurrency)

    async def one(doc_id: str) -> None:
        async with sem:
            async with SessionLocal() as session:
                run = await session.get(ExtractionRun, run_id)
                if run is None:
                    return
                try:
                    doc = await session.get(Document, doc_id)
                    if doc is None:
                        raise ValueError(f"unknown doc_id {doc_id}")
                    result = await extract_document(session, doc, run_id=run_id, replace=replace)
                    run.docs_done += 1
                    run.rules_extracted += len(result.rules)
                    run.input_tokens += result.input_tokens
                    run.output_tokens += result.output_tokens
                    run.events = [
                        *run.events,
                        {
                            "ts": dt.datetime.now(dt.UTC).isoformat(),
                            "doc_id": doc_id,
                            "event": "extracted",
                            "rules": len(result.rules),
                            "rejected": result.spans_rejected,
                        },
                    ]
                except Exception as exc:  # noqa: BLE001 - recorded per document
                    log.exception("extraction failed for %s", doc_id)
                    run.docs_failed += 1
                    run.events = [
                        *run.events,
                        {
                            "ts": dt.datetime.now(dt.UTC).isoformat(),
                            "doc_id": doc_id,
                            "event": "failed",
                            "detail": str(exc)[:500],
                        },
                    ]
                await session.commit()

    await asyncio.gather(*(one(d) for d in doc_ids))

    async with SessionLocal() as session:
        run = await session.get(ExtractionRun, run_id)
        if run is not None:
            run.status = "failed" if run.docs_failed == run.docs_total else "complete"
            run.finished_at = dt.datetime.now(dt.UTC)
            await session.commit()


async def rule_counts_by_doc(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(Rule.source_doc_id, func.count()).group_by(Rule.source_doc_id)
    )
    return {doc_id: n for doc_id, n in result if doc_id}
