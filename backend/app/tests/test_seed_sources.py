"""Tests for idempotent built-in source seeding (startup duplicate-key crash).

Regression: startup INSERT of default sources (LinkedIn / MyCareersFuture /
FastJobs) used to run without checking existing rows, so a restart against a
DB that already held e.g. a row named "MyCareersFuture" (with a different
domain) blew up on the unique ix_sources_name constraint.

Uses the live asyncpg database like the other DB-touching test modules
(DATABASE_URL override at invocation). Any row touched here is restored to its
original column values on teardown so the DB is left exactly as found.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.orm import Source
from app.services.seed_sources import BUILTIN_SOURCES, seed_builtin_sources


@pytest.fixture
async def db():
    # A test-local NullPool engine: every connection is bound to this test's
    # event loop (the shared app engine pools asyncpg connections across loops,
    # which raises "attached to a different loop" between tests).
    from app.db import settings

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _builtin_names(session) -> set[str]:
    names = {s["name"] for s in BUILTIN_SOURCES}
    rows = (await session.execute(select(Source.name))).scalars().all()
    return {n for n in rows if n in names}


async def test_seed_is_idempotent(db):
    # First run seeds whatever is missing; must not raise.
    await seed_builtin_sources(db)
    assert await _builtin_names(db) == {s["name"] for s in BUILTIN_SOURCES}

    # Second run: every builtin already present, so it inserts nothing and
    # must not raise on the unique name constraint.
    before = (await db.execute(select(Source.id))).scalars().all()
    await seed_builtin_sources(db)
    after = (await db.execute(select(Source.id))).scalars().all()
    assert len(after) == len(before)


async def test_seed_adopts_preexisting_name_with_other_domain(db):
    """A pre-existing builtin-named row whose domain does NOT match must be
    adopted, not duplicated — the exact production crash."""
    await seed_builtin_sources(db)  # ensure the row exists

    row = (
        await db.execute(select(Source).where(Source.name == "MyCareersFuture"))
    ).scalar_one()
    saved_domain, saved_base_url = row.domain, row.base_url
    row.domain = "legacy-mcf.example"
    row.base_url = "https://legacy.example/"
    await db.commit()
    try:
        await seed_builtin_sources(db)  # must not raise

        rows = (
            await db.execute(select(Source).where(Source.name == "MyCareersFuture"))
        ).scalars().all()
        assert len(rows) == 1  # no duplicate inserted
        assert rows[0].domain == "legacy-mcf.example"  # adopted, not overwritten
    finally:
        row = (
            await db.execute(select(Source).where(Source.name == "MyCareersFuture"))
        ).scalar_one()
        row.domain, row.base_url = saved_domain, saved_base_url
        await db.commit()
