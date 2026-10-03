"""Rental Housing Law Navigator - FastAPI application.

Mounted under /api:
  /api/rule-extraction   Module A - rule extraction
  /api/address-lookup    Module B - address lookup
  /api/change-tracking   Module C - change tracking
  /api/accounts          who is signed in, and their own saved buildings

WorkOS holds every account; this process only ever verifies a token's signature against
WorkOS's published keys, so it carries no WorkOS secret. An unconfigured checkout runs open
outside production - see app/core/auth.py for why, and what production does instead.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core import llm
from app.core.auth import auth, optional_principal
from app.core.config import settings
from app.core.db import engine
from app.modules.accounts.router import router as accounts_router
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
    if not auth.configured:
        # Loud, because the two modes differ in who may read the data. Outside production
        # an unconfigured checkout is the supported way to work; in production it is a
        # misconfiguration that locks everyone out rather than opening the door.
        log.warning(
            "WORKOS_CLIENT_ID is not set - the API is running OPEN and /api/accounts "
            "returns 503. Set it to require a signed-in caller."
        )
        if settings.environment == "production":
            log.error("WORKOS_CLIENT_ID is not set in production - every request is refused.")
    yield
    await auth.aclose()
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
        # Whether a caller needs a WorkOS token. The frontend reads this instead of
        # duplicating the rule, so the two can never disagree about who has to sign in.
        "auth_required": auth.configured or settings.environment == "production",
    }


@api.get("/meta", tags=["meta"])
async def meta() -> dict:
    """Everything the frontend needs to render filters without hardcoding."""
    from app.modules.accounts.schemas import ROLE_LABELS
    from app.modules.rule_extraction.schemas import Category, Level, RuleStatus

    return {
        "app": settings.app_name,
        "default_as_of": settings.default_as_of,
        "extraction_model": settings.extraction_model,
        "categories": [c.value for c in Category],
        "levels": [level.value for level in Level],
        "statuses": [s.value for s in RuleStatus],
        "results": ["applies", "does_not_apply", "unknown"],
        "roles": [{"role": r.value, "label": label} for r, label in ROLE_LABELS.items()],
        "disclaimer": "Not legal advice.",
    }


# The corpus modules answer the same questions for everyone who may ask, so the gate on
# them is authentication, not role: a renter and a housing agency reading the same rule get
# the same text and the same citation. What the role decides is which interface a person is
# given, and that is the frontend's business. The one place role appears in this API is the
# account that carries it.
signed_in = [Depends(optional_principal)]
api.include_router(module_a_router, dependencies=signed_in)
api.include_router(module_b_router, dependencies=signed_in)
api.include_router(module_c_router, dependencies=signed_in)
api.include_router(accounts_router)
app.include_router(api)
