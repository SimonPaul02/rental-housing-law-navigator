"""Rental Housing Law Navigator - FastAPI application.

Mounted under /api. Three module routers:
  /api/a  Module A - rule extraction
  /api/b  Module B - address lookup
  /api/c  Module C - change tracking
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core import llm
from app.core.config import settings
from app.core.db import engine
from app.modules.address_lookup.router import router as module_b_router
from app.modules.change_tracking.router import router as module_c_router
from app.modules.rule_extraction.router import router as module_a_router

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
log = logging.getLogger("rhln")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting %s (%s)", settings.app_name, settings.environment)
    if not llm.is_configured():
        log.warning(
            "ANTHROPIC_API_KEY is not set - Module A extraction will return 503. "
            "Modules B and C are unaffected."
        )
    yield
    await llm.aclose()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description=(
        "Which housing rules apply to an address on a given date, and how do "
        "supplied law-change cases affect the answer? Every answer cites its "
        "source text."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")


@api.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness plus a real database round-trip, for the Fly health check."""
    db_ok = True
    db_error = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        db_ok = False
        db_error = str(exc)[:200]

    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "database_error": db_error,
        "extraction_available": llm.is_configured(),
        "default_as_of": settings.default_as_of,
    }


@api.get("/meta", tags=["meta"])
async def meta() -> dict:
    """Everything the frontend needs to render filters without hardcoding."""
    from app.modules.rule_extraction.schemas import Category, Level, RuleStatus

    return {
        "app": settings.app_name,
        "default_as_of": settings.default_as_of,
        "extraction_model": settings.extraction_model,
        "categories": [c.value for c in Category],
        "levels": [level.value for level in Level],
        "statuses": [s.value for s in RuleStatus],
        "results": ["applies", "does_not_apply", "unknown"],
        "disclaimer": "Not legal advice.",
    }


api.include_router(module_a_router)
api.include_router(module_b_router)
api.include_router(module_c_router)
app.include_router(api)
