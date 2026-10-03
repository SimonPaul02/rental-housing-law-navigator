"""Module C routes - the five change-tracking tests and their export."""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.db.models import ChangeResult
from app.modules.change_tracking import service
from app.modules.change_tracking.schemas import (
    CanonicalMatch,
    ChangeTest,
    ChangeTestResult,
    RunTestsRequest,
)

router = APIRouter(prefix="/c", tags=["Module C - change tracking"])


@router.get("/tests", response_model=list[ChangeTest])
async def list_tests() -> list[ChangeTest]:
    """The supplied cases T1-T5, straight from dev/change_tests.json."""
    return service.load_tests()


@router.get("/tests/{test_id}", response_model=ChangeTest)
async def get_test(test_id: str) -> ChangeTest:
    test = service.get_test(test_id)
    if test is None:
        raise HTTPException(404, f"No test {test_id}")
    return test


@router.get("/canonical-rules", response_model=list[CanonicalMatch])
async def canonical_rules(
    session: AsyncSession = Depends(get_session),
) -> list[CanonicalMatch]:
    """How each challenge rule id maps onto our extracted records.

    Worth surfacing in the UI: if a canonical id has no match, every test that
    references it will report an empty set for an uninteresting reason.
    """
    out = []
    for canonical_id in service.CANONICAL_RULES:
        match, _ = await service.resolve_canonical(session, canonical_id)
        out.append(match)
    return out


@router.post("/tests/{test_id}/run", response_model=ChangeTestResult)
async def run_one(
    test_id: str,
    session: AsyncSession = Depends(get_session),
    persist: bool = True,
    address_limit: int = Query(500, le=500),
) -> ChangeTestResult:
    test = service.get_test(test_id)
    if test is None:
        raise HTTPException(404, f"No test {test_id}")
    return await service.run_test(session, test, address_limit=address_limit, persist=persist)


@router.post("/run", response_model=list[ChangeTestResult])
async def run_all(
    payload: RunTestsRequest, session: AsyncSession = Depends(get_session)
) -> list[ChangeTestResult]:
    tests = service.load_tests()
    if payload.test_ids:
        wanted = set(payload.test_ids)
        tests = [t for t in tests if t.test_id in wanted]
        if not tests:
            raise HTTPException(404, f"No tests matched {sorted(wanted)}")
    return [
        await service.run_test(
            session, t, address_limit=payload.address_limit, persist=payload.persist
        )
        for t in tests
    ]


@router.get("/results", response_model=list[ChangeTestResult])
async def list_results(
    session: AsyncSession = Depends(get_session),
) -> list[ChangeTestResult]:
    """Last stored result per test, with the test metadata rejoined."""
    rows = (
        (await session.execute(select(ChangeResult).order_by(ChangeResult.test_id))).scalars().all()
    )
    tests = {t.test_id: t for t in service.load_tests()}
    out = []
    for row in rows:
        test = tests.get(row.test_id)
        out.append(
            ChangeTestResult(
                test_id=row.test_id,
                title=test.title if test else row.test_id,
                type=row.test_type or (test.type if test else "unknown"),
                as_of=row.as_of,
                affected_address_ids=row.affected_address_ids,
                conflict_flag_address_ids=row.conflict_flag_address_ids,
                notes=row.notes or "",
                expected_behavior=test.expected_behavior if test else "",
                detail=row.detail,
            )
        )
    return out


@router.get("/export")
async def export_changes(
    session: AsyncSession = Depends(get_session),
    as_of: dt.date | None = None,
) -> Response:
    """submission_templates/changes.json shape."""
    stmt = select(ChangeResult).order_by(ChangeResult.test_id)
    if as_of:
        stmt = stmt.where(ChangeResult.as_of == as_of)
    rows = (await session.execute(stmt)).scalars().all()

    body: dict[str, dict] = {}
    for row in rows:
        entry: dict = {
            "affected_address_ids": row.affected_address_ids,
            "notes": row.notes or "",
        }
        if row.conflict_flag_address_ids:
            entry["conflict_flag_address_ids"] = row.conflict_flag_address_ids
        body[row.test_id] = entry

    return Response(
        content=json.dumps(body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="changes.json"'},
    )


@router.get("/stats")
async def change_stats(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(ChangeResult))).scalars().all()
    return {
        "tests_defined": len(service.load_tests()),
        "tests_run": len({r.test_id for r in rows}),
        "total_affected": sum(len(r.affected_address_ids) for r in rows),
        "total_conflicts": sum(len(r.conflict_flag_address_ids) for r in rows),
        "by_test": {
            r.test_id: {
                "affected": len(r.affected_address_ids),
                "conflicts": len(r.conflict_flag_address_ids),
                "as_of": r.as_of.isoformat(),
            }
            for r in rows
        },
    }
