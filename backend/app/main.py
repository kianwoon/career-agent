"""Career Agent FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes.routes import router
from app.api.routes.sources import router as sources_router
from app.config import get_settings
from app.services.browser import browser_service

settings = get_settings()

logger = logging.getLogger(__name__)


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
    except Exception as exc:  # startup must not hard-crash on DB hiccups
        logger.error("Could not initialize DB tables: %s", exc)
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


@app.get("/")
async def root() -> dict:
    return {"service": settings.app_name, "docs": "/docs", "api": "/api/v1"}
