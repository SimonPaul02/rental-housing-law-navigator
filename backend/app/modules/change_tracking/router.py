"""Module C routes - the five change-tracking tests and their export."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.db.models import Address
from app.modules.change_tracking import service
from app.modules.change_tracking.schemas import (
    CanonicalMatch,
    ChangeTest,
    ChangeTestResult,
    RunTestsRequest,
)
from app.modules.change_tracking.validation import (
    REQUIRED_TEST_IDS,
    sample_address_ids,
    validate_changes,
)

router = APIRouter(prefix="/change-tracking", tags=["Module C - change tracking"])


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
    try:
        return await service.run_test(session, test, address_limit=address_limit, persist=persist)
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc


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
    try:
        return [
            await service.run_test(
                session, t, address_limit=payload.address_limit, persist=payload.persist
            )
            for t in tests
        ]
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/results", response_model=list[ChangeTestResult])
async def list_results(
    session: AsyncSession = Depends(get_session),
) -> list[ChangeTestResult]:
    """Current results only; stored rows may describe older rule versions."""
    try:
        return [await service.run_test(session, t, persist=False) for t in service.load_tests()]
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/export")
async def export_changes(
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Build all five tests from current inputs; never mix persisted partial runs."""
    tests = service.load_tests()
    if {t.test_id for t in tests} != set(REQUIRED_TEST_IDS):
        raise HTTPException(409, "The five required change definitions are incomplete.")
    addresses = set((await session.execute(select(Address.address_id))).scalars())
    expected_addresses = sample_address_ids(settings.addresses_csv)
    if addresses != expected_addresses:
        raise HTTPException(409, "Database addresses differ from the 500 supplied sample IDs.")
    body: dict[str, dict] = {}
    try:
        results = [await service.run_test(session, test, persist=False) for test in tests]
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc
    for row in results:
        entry: dict = {
            "affected_address_ids": row.affected_address_ids,
            "notes": row.notes,
        }
        if row.conflict_flag_address_ids:
            entry["conflict_flag_address_ids"] = row.conflict_flag_address_ids
        body[row.test_id] = entry

    problems = validate_changes(body, address_ids=addresses)
    if problems:
        raise HTTPException(
            409, {"detail": "Change export failed validation", "problems": problems}
        )

    return Response(
        content=json.dumps(body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="changes.json"'},
    )


@router.get("/stats")
async def change_stats(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        rows = [await service.run_test(session, t, persist=False) for t in service.load_tests()]
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc
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
