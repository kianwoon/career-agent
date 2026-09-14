"""Tests for background-task watchdog crash handling and startup reconciliation."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Mirror test_compat_routes: isolate API key config, then run the app on
    # its own event loop so asyncpg pools bind to the right loop.
    saved = {k: os.environ.get(k) for k in ("API_KEYS", "API_RATE_LIMIT_PER_MIN")}
    os.environ["API_KEYS"] = "test-watchdog-key:1000"
    os.environ["API_RATE_LIMIT_PER_MIN"] = "1000"
    from app.api import security
    from app.db import engine

    get_settings.cache_clear()
    security._key_store = None
    with TestClient(app) as c:
        yield c
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    security._key_store = None
    get_settings.cache_clear()

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(engine.dispose())
    loop.close()


async def _seed_task(status: str = "running") -> str:
    from app.db import async_session
    from app.models.orm import SearchTask

    task_id = str(uuid.uuid4())
    async with async_session() as db:
        db.add(
            SearchTask(
                id=task_id,
                type="job_search",
                query="watchdog test",
                status=status,
            )
        )
        await db.commit()
    return task_id


async def _get_task(task_id: str):
    from app.db import async_session
    from app.models.orm import SearchTask

    async with async_session() as db:
        return await db.get(SearchTask, task_id)


async def _run_watchdog_with_crash(task_id: str) -> None:
    from unittest.mock import patch

    from app.api.routes import routes

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom in pipeline")

    with patch.object(routes, "_run_task", _boom):
        await routes._run_task_with_watchdog(
            task_id, routes.SearchType.jobs, "watchdog test", "Singapore"
        )


def test_watchdog_marks_crashed_task_failed(client):
    task_id = client.portal.call(_seed_task, "running")
    # The pipeline raises a generic (non-timeout) exception; the catch-all
    # must fail the task instead of leaving it 'running' forever.
    client.portal.call(_run_watchdog_with_crash, task_id)
    task = client.portal.call(_get_task, task_id)
    assert task.status == "failed"
    assert "Pipeline crashed" in (task.error or "")
    assert task.completed_at is not None


async def _run_reconcile() -> None:
    from app.main import reconcile_orphaned_tasks

    await reconcile_orphaned_tasks()


def test_startup_reconciliation_fails_running_task(client):
    task_id = client.portal.call(_seed_task, "running")
    client.portal.call(_run_reconcile)
    task = client.portal.call(_get_task, task_id)
    assert task.status == "failed"
    assert task.error == "Interrupted by server restart"
    assert task.completed_at is not None
