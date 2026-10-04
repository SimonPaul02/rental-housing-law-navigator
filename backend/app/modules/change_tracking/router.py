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
    build_changes_export,
    sample_address_ids,
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
    references it comes back blocked, naming the id.
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
        [result] = await service.run_tests(
            session, [test], address_limit=address_limit, persist=persist
        )
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc
    return result


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
        return await service.run_tests(
            session, tests, address_limit=payload.address_limit, persist=payload.persist
        )
    except service.ChangeInputError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/results", response_model=list[ChangeTestResult])
async def list_results(
    session: AsyncSession = Depends(get_session),
) -> list[ChangeTestResult]:
    """Current results only; stored rows may describe older rule versions.

    Always one entry per case. A case that cannot be answered is `blocked`
    with its reason - read its empty sets as "not computed", never as
    "nothing moved".
    """
    return await service.run_tests(session, service.load_tests())


@router.get("/export")
async def export_changes(
    session: AsyncSession = Depends(get_session),
    strict: bool = Query(
        False,
        description=(
            "Refuse unless all five cases are complete - the bar for the frozen "
            "submission. Otherwise blocked cases are left out and named in "
            "X-Changes-Omitted."
        ),
    ),
) -> Response:
    """Build every case from current inputs; never mix persisted partial runs.

    A case that cannot be answered is left out of the file rather than written
    as an empty list, because an empty list would claim no address is affected.
    """
    tests = service.load_tests()
    if strict and {t.test_id for t in tests} != set(REQUIRED_TEST_IDS):
        raise HTTPException(409, "The five required change definitions are incomplete.")
    addresses = set((await session.execute(select(Address.address_id))).scalars())
    expected_addresses = sample_address_ids(settings.addresses_csv)
    if addresses != expected_addresses:
        raise HTTPException(409, "Database addresses differ from the 500 supplied sample IDs.")

    results = await service.run_tests(session, tests, address_ids=addresses)
    export = build_changes_export(results, address_ids=addresses)
    if not export.body or (strict and not export.complete):
        raise HTTPException(
            409,
            {
                "detail": (
                    "No change case can be exported yet"
                    if not export.body
                    else "Change export is incomplete"
                ),
                "problems": export.problems,
            },
        )

    partial = sorted(
        r.test_id for r in results if r.status == "partial" and r.test_id in export.body
    )
    return Response(
        content=json.dumps(export.body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="changes.json"',
            "X-Changes-Complete": "true" if export.complete else "false",
            "X-Changes-Omitted": ",".join(sorted(export.omitted)),
            "X-Changes-Partial": ",".join(partial),
        },
    )


@router.get("/stats")
async def change_stats(session: AsyncSession = Depends(get_session)) -> dict:
    tests = service.load_tests()
    rows = await service.run_tests(session, tests)
    answered = [r for r in rows if r.status != "blocked"]
    return {
        "tests_defined": len(tests),
        "tests_run": len(rows),
        "tests_answered": len(answered),
        "blocked": [r.test_id for r in rows if r.status == "blocked"],
        "partial": [r.test_id for r in rows if r.status == "partial"],
        "total_affected": sum(len(r.affected_address_ids) for r in answered),
        "total_conflicts": sum(len(r.conflict_flag_address_ids) for r in answered),
        "by_test": {
            r.test_id: {
                "affected": len(r.affected_address_ids),
                "conflicts": len(r.conflict_flag_address_ids),
                "as_of": r.as_of.isoformat(),
                "status": r.status,
            }
            for r in rows
        },
    }
