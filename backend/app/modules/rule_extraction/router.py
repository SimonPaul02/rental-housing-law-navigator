"""Module A routes - corpus browsing, extraction, rule CRUD, export."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, get_session
from app.core.llm import LLMNotConfiguredError, is_configured
from app.db.models import Document, ExtractionRun, Rule
from app.modules.rule_extraction import service
from app.modules.rule_extraction.schemas import (
    DocumentDetail,
    DocumentSummary,
    ExtractDocResult,
    ExtractRequest,
    RuleRecord,
    RuleStats,
    RunSummary,
)

router = APIRouter(prefix="/rule-extraction", tags=["Module A - rule extraction"])


# ---------------------------------------------------------------- corpus ---
@router.get("/corpus/documents", response_model=list[DocumentSummary])
async def list_documents(
    session: AsyncSession = Depends(get_session),
    jurisdiction: str | None = Query(None, description="Substring match."),
    has_text: bool | None = Query(None),
    status: str | None = Query(None),
) -> list[DocumentSummary]:
    stmt = select(Document).order_by(Document.doc_id)
    if jurisdiction:
        stmt = stmt.where(Document.jurisdictions.ilike(f"%{jurisdiction}%"))
    if status:
        stmt = stmt.where(Document.status == status)
    if has_text is True:
        stmt = stmt.where(Document.body.isnot(None))
    elif has_text is False:
        stmt = stmt.where(Document.body.is_(None))

    docs = (await session.execute(stmt)).scalars().all()
    counts = await service.rule_counts_by_doc(session)
    return [
        DocumentSummary(
            **{
                k: getattr(d, k)
                for k in (
                    "doc_id",
                    "jurisdictions",
                    "url",
                    "source_type",
                    "capture",
                    "status",
                    "text_file",
                )
            },
            has_text=d.has_text,
            rule_count=counts.get(d.doc_id, 0),
        )
        for d in docs
    ]


@router.get("/corpus/documents/{doc_id}", response_model=DocumentDetail)
async def get_document(doc_id: str, session: AsyncSession = Depends(get_session)) -> DocumentDetail:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, f"No document {doc_id}")
    counts = await service.rule_counts_by_doc(session)
    return DocumentDetail(
        **{
            k: getattr(doc, k)
            for k in (
                "doc_id",
                "jurisdictions",
                "url",
                "source_type",
                "capture",
                "status",
                "text_file",
                "retrieved_at",
                "sha256",
                "body",
            )
        },
        has_text=doc.has_text,
        rule_count=counts.get(doc.doc_id, 0),
    )


# ------------------------------------------------------------ extraction ---
@router.post("/extract/{doc_id}", response_model=ExtractDocResult)
async def extract_one(
    doc_id: str,
    replace: bool = Query(True),
    session: AsyncSession = Depends(get_session),
) -> ExtractDocResult:
    """Extract a single document synchronously. Fast enough to await."""
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, f"No document {doc_id}")
    if not doc.body:
        raise HTTPException(
            422, f"{doc_id} is link-only ({doc.capture}); no supplied text to read."
        )
    try:
        return await service.extract_document(session, doc, replace=replace)
    except LLMNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/extract", response_model=RunSummary, status_code=202)
async def extract_batch(
    payload: ExtractRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> RunSummary:
    """Kick off a corpus pass in the background and return a run id.

    This is why the backend is a persistent container rather than a serverless
    function: the full pass takes far longer than any function timeout.
    """
    if not is_configured():
        raise HTTPException(
            503,
            "ANTHROPIC_API_KEY is not set. Module A extraction is unavailable.",
        )

    if payload.doc_ids:
        stmt = select(Document.doc_id).where(Document.doc_id.in_(payload.doc_ids))
    else:
        stmt = select(Document.doc_id).where(Document.body.isnot(None))
    doc_ids = list((await session.execute(stmt.order_by(Document.doc_id))).scalars())

    if not doc_ids:
        raise HTTPException(422, "No documents with supplied text matched.")

    run = await service.start_run(session, doc_ids, settings.extraction_model)
    await session.commit()
    background.add_task(service.run_extraction, run.run_id, doc_ids, payload.replace)
    return RunSummary.model_validate(run)


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(session: AsyncSession = Depends(get_session)) -> list[RunSummary]:
    stmt = select(ExtractionRun).order_by(ExtractionRun.created_at.desc()).limit(50)
    runs = (await session.execute(stmt)).scalars().all()
    return [RunSummary.model_validate(r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> RunSummary:
    run = await session.get(ExtractionRun, run_id)
    if run is None:
        raise HTTPException(404, f"No run {run_id}")
    return RunSummary.model_validate(run)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """Server-sent events for live extraction progress in the demo."""

    async def events():
        seen = 0
        while True:
            async with SessionLocal() as session:
                run = await session.get(ExtractionRun, run_id)
                if run is None:
                    yield f"event: error\ndata: {json.dumps({'detail': 'unknown run'})}\n\n"
                    return
                for item in run.events[seen:]:
                    yield f"event: document\ndata: {json.dumps(item)}\n\n"
                seen = len(run.events)
                payload = {
                    "run_id": run.run_id,
                    "status": run.status,
                    "docs_total": run.docs_total,
                    "docs_done": run.docs_done,
                    "docs_failed": run.docs_failed,
                    "rules_extracted": run.rules_extracted,
                }
                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                if run.status != "running":
                    yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                    return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ----------------------------------------------------------------- rules ---
@router.get("/rules", response_model=list[RuleRecord])
async def list_rules(
    session: AsyncSession = Depends(get_session),
    jurisdiction: str | None = None,
    level: str | None = None,
    category: str | None = None,
    status: str | None = None,
    doc_id: str | None = None,
    limit: int = Query(500, le=2000),
    offset: int = 0,
) -> list[RuleRecord]:
    stmt = select(Rule).order_by(Rule.team_rule_id)
    if jurisdiction:
        stmt = stmt.where(Rule.jurisdiction.ilike(f"%{jurisdiction}%"))
    if level:
        stmt = stmt.where(Rule.level == level)
    if category:
        stmt = stmt.where(Rule.category == category)
    if status:
        stmt = stmt.where(Rule.status == status)
    if doc_id:
        stmt = stmt.where(Rule.source_doc_id == doc_id)
    rules = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [RuleRecord.model_validate(r) for r in rules]


@router.get("/rules/export")
async def export_rules(session: AsyncSession = Depends(get_session)) -> Response:
    """submission_templates/rules.json shape."""
    rules = (await session.execute(select(Rule).order_by(Rule.team_rule_id))).scalars().all()
    body = {"rules": [RuleRecord.model_validate(r).model_dump() for r in rules]}
    return Response(
        content=json.dumps(body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="rules.json"'},
    )


@router.get("/rules/{team_rule_id}", response_model=RuleRecord)
async def get_rule(team_rule_id: str, session: AsyncSession = Depends(get_session)) -> RuleRecord:
    rule = await session.get(Rule, team_rule_id)
    if rule is None:
        raise HTTPException(404, f"No rule {team_rule_id}")
    return RuleRecord.model_validate(rule)


@router.patch("/rules/{team_rule_id}", response_model=RuleRecord)
async def patch_rule(
    team_rule_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> RuleRecord:
    """Human review: flag a conflict, adjust an override list, fix a status."""
    rule = await session.get(Rule, team_rule_id)
    if rule is None:
        raise HTTPException(404, f"No rule {team_rule_id}")
    editable = {
        "status",
        "overrides",
        "interaction",
        "conflict_flag",
        "conflict_note",
        "coverage_conditions",
        "exemptions",
        "key_value",
        "effective_date",
        "confidence",
    }
    unknown = set(payload) - editable
    if unknown:
        raise HTTPException(422, f"Not editable: {sorted(unknown)}")
    for key, value in payload.items():
        setattr(rule, key, value)
    await session.flush()
    return RuleRecord.model_validate(rule)


@router.delete("/rules/{team_rule_id}", status_code=204)
async def delete_rule(team_rule_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    result = await session.execute(delete(Rule).where(Rule.team_rule_id == team_rule_id))
    if result.rowcount == 0:
        raise HTTPException(404, f"No rule {team_rule_id}")
    return Response(status_code=204)


@router.get("/stats", response_model=RuleStats)
async def rule_stats(session: AsyncSession = Depends(get_session)) -> RuleStats:
    async def group(column) -> dict[str, int]:
        rows = await session.execute(select(column, func.count()).group_by(column))
        return {str(k): n for k, n in rows}

    total = (await session.execute(select(func.count()).select_from(Rule))).scalar_one()
    docs_total = (await session.execute(select(func.count()).select_from(Document))).scalar_one()
    docs_text = (
        await session.execute(
            select(func.count()).select_from(Document).where(Document.body.isnot(None))
        )
    ).scalar_one()
    docs_extracted = (
        await session.execute(select(func.count(func.distinct(Rule.source_doc_id))))
    ).scalar_one()
    flagged = (
        await session.execute(
            select(func.count()).select_from(Rule).where(Rule.conflict_flag.is_(True))
        )
    ).scalar_one()

    return RuleStats(
        total=total,
        by_category=await group(Rule.category),
        by_status=await group(Rule.status),
        by_level=await group(Rule.level),
        by_jurisdiction=await group(Rule.jurisdiction),
        documents_total=docs_total,
        documents_with_text=docs_text,
        documents_extracted=docs_extracted,
        flagged_conflicts=flagged,
    )
