"""Career Agent FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes.routes import router
from app.api.routes.sources import router as sources_router
from app.config import get_settings
from app.services.browser import browser_service

settings = get_settings()

logger = logging.getLogger(__name__)


async def reconcile_orphaned_tasks() -> None:
    """Fail tasks left pending/running by a previous server process.

    Background pipelines are launched with asyncio.create_task and are lost
    on restart, so such tasks would otherwise stay 'running' forever.
    """
    from sqlalchemy import update

    from app.db import async_session
    from app.models.orm import SearchTask
    from app.models.schemas import TaskStatus

    async with async_session() as db:
        result = await db.execute(
            update(SearchTask)
            .where(SearchTask.status.in_(["pending", "running"]))
            .values(
                status=TaskStatus.failed.value,
                error="Interrupted by server restart",
                completed_at=datetime.utcnow(),
            )
        )
        await db.commit()
        if result.rowcount:
            logger.info("Reconciled %d orphaned task(s) as failed", result.rowcount)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Attach a handler AFTER uvicorn's logging dictConfig runs (uvicorn
    # configures at startup, wiping basicConfig handlers set at import time).
    _configure_handlers()
    # Create DB tables on startup (idempotent). In production this runs against
    # the managed Postgres; harmless to re-run on each boot.
    try:
        from app.db import Base, engine
        from app.models import orm  # noqa: F401  (register models)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured")
        # Seed built-in sources (LinkedIn, MyCareersFuture, FastJobs) as real
        # rows so they appear as cards, can be disabled, and hold sessions.
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.services.seed_sources import seed_builtin_sources

        async with AsyncSession(engine) as db:
            await seed_builtin_sources(db)
    except Exception as exc:  # startup must not hard-crash on DB hiccups
        logger.error("Could not initialize DB tables: %s", exc)
    # Reconcile tasks orphaned by a previous process: background pipelines
    # run as asyncio.create_task and do NOT survive a restart, so any task
    # left pending/running is dead — mark it failed so the UI stops waiting.
    try:
        await reconcile_orphaned_tasks()
    except Exception as exc:
        logger.error("Could not reconcile orphaned tasks: %s", exc)
    # Startup: nothing heavy yet. Shutdown: close browser sessions.
    yield
    await browser_service.shutdown()


def _configure_handlers() -> None:
    """Attach a stream handler to our loggers so INFO logs are visible."""
    import sys

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in (
        "app",
        "app.services.pacing",
        "app.services.cache",
        "app.services.linkedin",
        "app.services.linkedin_people",
        "app.services.browser",
        "app.agent",
        "app.api",
    ):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # avoid root handler duplicates
        # Avoid duplicate handlers on reload.
        if not logger.handlers:
            logger.addHandler(handler)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(sources_router, prefix="/api/v1", dependencies=router.dependencies)

# Browser-extension agent (HTTP polling — reliable across MV3 suspensions).
# No auth dependency: poll reveals only commands the API itself queued for
# the user's own extension, and result only resolves those command ids.
from app.api.routes.agent import router as agent_router

# Koyeb's route rule "/api -> career-api:8000" STRIPS the /api prefix before
# forwarding, so requests arrive here as /v1/... — mount both spellings.
app.include_router(agent_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/v1")
app.include_router(sources_router, prefix="/v1", dependencies=router.dependencies)
app.include_router(router, prefix="/v1")
# Koyeb strips the leading /api from documented URLs before they reach us:
#   documented  /api/v1/search/candidates   → arrives as /v1/search/candidates
#   deployed    /api/v1/api/v1/...          → arrives as /v1/api/v1/...
# The main router carries an INTERNAL prefix="/api/v1", so neither spelling
# matched /v1/search/candidates and every documented external call 404'd.
# Rewrite stripped paths to the router's internal prefix instead of stacking
# more duplicate mounts.
@app.middleware("http")
async def _restore_stripped_api_prefix(request: Request, call_next: Any) -> Any:
    path = request.scope.get("path", "")
    if path.startswith("/v1/") and not path.startswith("/v1/api/"):
        request.scope["path"] = "/api/v1" + path[len("/v1"):]
    return await call_next(request)


@app.get("/")
async def root() -> dict:
    return {"service": settings.app_name, "docs": "/docs", "api": "/api/v1"}


# Koyeb strips the leading /api, so an external caller polling
# /opportunities/{oid}/external-candidates/search/{task_id} arrives here
# without any /api/v1 prefix — register the compat shim directly on the app
# (with the same API-key dependency as the router) for both spellings.
from fastapi import Depends

from app.api.security import require_api_key


def _register_external_compat(path: str) -> None:
    from app.api.routes.routes import _task_results_payload
    from app.db import get_db
    from app.models.schemas import SearchTaskResult

    async def _compat(
        opportunity_id: str,
        task_id: str,
        db: Any = Depends(get_db),
    ) -> SearchTaskResult:
        """Compat shim for external system path convention; opportunity_id ignored."""
        return await _task_results_payload(task_id, db)

    # Give the two registrations distinct function names for OpenAPI clarity.
    _compat.__name__ = path.strip("/").replace("/", "_")
    app.get(path, response_model=SearchTaskResult, dependencies=[Depends(require_api_key)])(
        _compat
    )


_register_external_compat("/opportunities/{opportunity_id}/external-candidates/search/{task_id}")
_register_external_compat("/api/opportunities/{opportunity_id}/external-candidates/search/{task_id}")
