"""Module A: turn corpus documents into structured rule records.

Extraction is automated end to end - the model reads the supplied document
text and returns schema-shaped records. Nothing here hand-codes a rule.

Two guards matter for scoring:
  1. Every rule must carry a `quoted_span` that really occurs in the source
     document. We verify that in code and drop rules that fail, so a
     hallucinated citation can never reach the submission.
  2. team_rule_id is assigned by us, not the model, and is derived from what
     the rule *is* rather than from a counter, so re-extracting a document
     leaves every id - and every lookups.json reference to it - unchanged.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import uuid

from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm import get_client
from app.db.models import Document, ExtractionRun, Rule
from app.modules.rule_extraction.pipeline import models as model_specs
from app.modules.rule_extraction.pipeline.merge import group_key_for, rule_id_for
from app.modules.rule_extraction.pipeline.text import unwrap
from app.modules.rule_extraction.schemas import (
    DocumentExtraction,
    ExtractDocResult,
    RuleImportRejection,
    RuleImportResult,
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


def stable_rule_id(
    doc_id: str, jurisdiction: str, category: str, citation: str, ordinal: int
) -> str:
    """An id derived from what the rule is, not from the order it was found in.

    A counter cannot survive re-extraction: `replace=True` deletes a
    document's rules and the next pass renumbers them, so every
    `team_rule_id` already written into lookups.json or changes.json silently
    points at a different rule - or at nothing.

    `pipeline.merge` already defines the identity of a law (jurisdiction,
    category, citation) and hashes it; this reuses that definition and adds
    two parts:

      * the document, because until the merge stage is wired in, two
        documents describing one law are still two records and must not
        collide on the primary key;
      * an ordinal, for the case where one document draws several separately
        actionable obligations from a single citation.
    """
    group_key = group_key_for(jurisdiction, category, citation)
    return rule_id_for(f"{doc_id.strip().casefold()}|{group_key}|{ordinal}")


def _assign_ids(doc_id: str, extracted: list) -> dict[int, str]:
    """Map each record's position to its id.

    Ordinals are handed out in a sorted order rather than the order the model
    happened to return, so the same set of obligations gets the same ids on
    every pass.
    """
    groups: dict[str, list[int]] = {}
    for index, rule in enumerate(extracted):
        key = group_key_for(rule.jurisdiction, rule.category.value, rule.citation)
        groups.setdefault(key, []).append(index)

    ids: dict[int, str] = {}
    for indexes in groups.values():
        ordered = sorted(
            indexes, key=lambda i: (extracted[i].title or "", extracted[i].quoted_span)
        )
        for ordinal, index in enumerate(ordered):
            rule = extracted[index]
            ids[index] = stable_rule_id(
                doc_id, rule.jurisdiction, rule.category.value, rule.citation, ordinal
            )
    return ids


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
    spec = model_specs.spec_for(settings.extraction_model)
    # The model is shown the body with the extractor's hard line breaks undone,
    # so a quoted span comes back as a whole sentence instead of one 77-column
    # line. Verification below still runs against the stored original, which is
    # safe because unwrapping only ever replaces a newline with a space and the
    # span check normalises whitespace anyway.
    body = unwrap(doc.body)
    if len(doc.body) // 3 > spec.context_tokens:
        raise ValueError(
            f"{doc.doc_id} is too large for {spec.model_id} ({spec.context_tokens:,} token context)"
        )

    extra: dict = {}
    if spec.supports_adaptive_thinking:
        # Opus 5 runs adaptive by default; Sonnet must be asked. Haiku 4.5
        # rejects the parameter outright, hence the capability check.
        extra["thinking"] = {"type": "adaptive"}

    response = await client.messages.parse(
        model=spec.model_id,
        max_tokens=settings.extraction_max_tokens,
        **extra,
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
                    f"{body}\n"
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

    # Keep what the model said about the document, not just what it extracted.
    # For a document that yielded nothing this note is the whole answer to
    # "why is this 0", and it used to be discarded with the response.
    doc.document_note = parsed.document_note

    if replace:
        await session.execute(delete(Rule).where(Rule.source_doc_id == doc.doc_id))
        await session.flush()

    verified = []
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
        verified.append(extracted)

    ids = _assign_ids(doc.doc_id, verified)
    kept: list[RuleRecord] = []

    for index, extracted in enumerate(verified):
        rule = Rule(
            team_rule_id=ids[index],
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


async def _record_progress(
    session: AsyncSession, run_id: str, event: dict, **increments: int
) -> None:
    """Add to the run's counters and event log without reading them first.

    Every document runs in its own session, and `extraction_concurrency` of
    them run at once. Incrementing in Python - `run.docs_done += 1` on an
    ORM-loaded row - makes each one read, add and write back, so two
    documents that overlap both write the same value and one increment is
    lost. A 54-document pass at concurrency 4 lost 39 of them, reporting
    15/54 done with 0 failed while all 54 had in fact been extracted.

    Postgres can do the arithmetic instead: `SET docs_done = docs_done + 1`
    and `events = events || <entry>` resolve against the row as it is at
    write time, so concurrent updates add up rather than overwrite. The
    events log is append-only and feeds the SSE progress stream, which is
    the other thing the lost updates were silently truncating.
    """
    values: dict = {
        column: getattr(ExtractionRun, column) + amount for column, amount in increments.items()
    }
    values["events"] = ExtractionRun.events.concat(cast([event], JSONB))
    await session.execute(
        update(ExtractionRun).where(ExtractionRun.run_id == run_id).values(**values)
    )


async def run_extraction(run_id: str, doc_ids: list[str], replace: bool) -> None:
    """Background corpus pass. Each document gets its own session and commit,
    so progress survives a failure partway through and the SSE endpoint can
    watch the row advance."""
    from app.core.db import SessionLocal

    sem = asyncio.Semaphore(settings.extraction_concurrency)

    async def one(doc_id: str) -> None:
        async with sem:
            async with SessionLocal() as session:
                now = lambda: dt.datetime.now(dt.UTC).isoformat()  # noqa: E731
                try:
                    doc = await session.get(Document, doc_id)
                    if doc is None:
                        raise ValueError(f"unknown doc_id {doc_id}")
                    result = await extract_document(session, doc, run_id=run_id, replace=replace)
                    await _record_progress(
                        session,
                        run_id,
                        {
                            "ts": now(),
                            "doc_id": doc_id,
                            "event": "extracted",
                            "rules": len(result.rules),
                            "rejected": result.spans_rejected,
                        },
                        docs_done=1,
                        rules_extracted=len(result.rules),
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                    )
                except Exception as exc:  # noqa: BLE001 - recorded per document
                    log.exception("extraction failed for %s", doc_id)
                    # The failed document's own writes must go, but the
                    # progress note has to survive - so roll back first.
                    await session.rollback()
                    await _record_progress(
                        session,
                        run_id,
                        {
                            "ts": now(),
                            "doc_id": doc_id,
                            "event": "failed",
                            "detail": str(exc)[:500],
                        },
                        docs_failed=1,
                    )
                await session.commit()

    await asyncio.gather(*(one(d) for d in doc_ids))

    async with SessionLocal() as session:
        run = await session.get(ExtractionRun, run_id)
        if run is not None:
            run.status = "failed" if run.docs_failed == run.docs_total else "complete"
            run.finished_at = dt.datetime.now(dt.UTC)
            await session.commit()


IMPORTABLE = (
    "jurisdiction",
    "level",
    "category",
    "status",
    "title",
    "requirement",
    "key_value",
    "coverage_conditions",
    "exemptions",
    "overrides",
    "interaction",
    "effective_date",
    "citation",
    "source_doc_id",
    "source_url",
    "quoted_span",
    "confidence",
    "conflict_flag",
    "conflict_note",
)


async def import_rules(
    session: AsyncSession,
    records: list[RuleRecord],
    *,
    replace: bool,
    document_notes: dict[str, str] | None = None,
) -> RuleImportResult:
    """Load records produced by an extraction pass run elsewhere.

    The span guard runs again here rather than being trusted from the payload.
    An imported record has to quote text that occurs in *this* deployment's
    copy of its source document, so an import cannot put a rule into the
    submission that the corpus does not support - the same promise a locally
    extracted rule carries, and the reason this is not a plain bulk insert.

    Ids are content-addressed, so importing the same pass twice updates the
    rows in place instead of duplicating them.
    """
    doc_ids = {r.source_doc_id for r in records if r.source_doc_id}
    bodies = dict(
        (
            await session.execute(
                select(Document.doc_id, Document.body).where(Document.doc_id.in_(doc_ids))
            )
        ).all()
    )

    deleted = 0
    if replace:
        result = await session.execute(delete(Rule))
        deleted = result.rowcount or 0
        await session.flush()

    inserted = updated = 0
    rejected: list[RuleImportRejection] = []

    for record in records:
        if not record.source_doc_id:
            rejected.append(
                RuleImportRejection(team_rule_id=record.team_rule_id, reason="no source_doc_id")
            )
            continue
        if record.source_doc_id not in bodies:
            rejected.append(
                RuleImportRejection(
                    team_rule_id=record.team_rule_id,
                    reason=f"unknown document {record.source_doc_id} in this deployment",
                )
            )
            continue
        if not span_occurs_in(record.quoted_span, bodies[record.source_doc_id] or ""):
            rejected.append(
                RuleImportRejection(
                    team_rule_id=record.team_rule_id,
                    reason=f"quoted_span does not occur in {record.source_doc_id}",
                )
            )
            continue

        values = {field: getattr(record, field) for field in IMPORTABLE}
        existing = None if replace else await session.get(Rule, record.team_rule_id)
        if existing is None:
            session.add(Rule(team_rule_id=record.team_rule_id, **values))
            inserted += 1
        else:
            for field, value in values.items():
                setattr(existing, field, value)
            updated += 1

    # The notes belong to the same pass as the rules: a document that yielded
    # nothing has no row in `records` to carry its explanation, so it travels
    # separately or not at all.
    notes_applied = 0
    for doc_id, note in (document_notes or {}).items():
        doc = await session.get(Document, doc_id)
        if doc is not None:
            doc.document_note = note
            notes_applied += 1

    await session.flush()
    total = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()

    return RuleImportResult(
        received=len(records),
        inserted=inserted,
        updated=updated,
        deleted=deleted,
        rejected=rejected,
        total_after=total,
        notes_applied=notes_applied,
    )


async def rule_counts_by_doc(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(Rule.source_doc_id, func.count()).group_by(Rule.source_doc_id)
    )
    return {doc_id: n for doc_id, n in result if doc_id}
