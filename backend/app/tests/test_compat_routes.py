"""Tests for the external-candidates compat routes (Expressautomate-style paths)."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    # Other test modules (notably test_security) set API_KEYS at import time
    # and rely on it still being set later. Save and restore so module order
    # doesn't leak our key config into them.
    saved = {k: os.environ.get(k) for k in ("API_KEYS", "API_RATE_LIMIT_PER_MIN")}
    os.environ["API_KEYS"] = "test-compat-key:1000"
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
    # The TestClient's lifespan exited its event loop; pooled asyncpg
    # connections are bound to that loop. Drop them so later modules that
    # run their own loops (e.g. test_graph via asyncio.run) get fresh ones.
    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(engine.dispose())
    loop.close()


def _headers():
    return {"X-API-Key": "test-compat-key"}


async def _seed_completed_task_with_results() -> str:
    from app.db import async_session
    from app.models.orm import Candidate, MatchEvaluation, SearchTask, User

    task_id = str(uuid.uuid4())
    async with async_session() as db:
        user = User(id=str(uuid.uuid4()), email=f"{task_id}@compat.test")
        db.add(user)
        await db.flush()
        candidate = Candidate(
            id=str(uuid.uuid4()),
            user_id=user.id,
            name="Compat Test Candidate",
            headline="Backend engineer",
            skills=["python"],
            experience="5 years",
            education="",
            certifications="",
            source="test",
            source_url="https://example.com/c/1",
        )
        db.add(candidate)
        task = SearchTask(
            id=task_id,
            type="candidate_search",
            query="compat test",
            status="completed",
        )
        db.add(task)
        await db.flush()
        db.add(
            MatchEvaluation(
                task_id=task_id,
                entity_type="candidate",
                entity_id=candidate.id,
                score=90.0,
                reason="test match",
            )
        )
        await db.commit()
    return task_id


def _compat_paths(oid: str, tid: str) -> list[str]:
    return [
        f"/opportunities/{oid}/external-candidates/search/{tid}",
        f"/api/opportunities/{oid}/external-candidates/search/{tid}",
    ]


def test_compat_route_returns_results(client):
    # Seed on the TestClient's event loop (the app's async engine binds its
    # connections to that loop; a separate pytest-asyncio loop would fail).
    task_id = client.portal.call(_seed_completed_task_with_results)
    for path in _compat_paths("their-opp-123", task_id):
        r = client.get(path, headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_id"] == task_id
        assert isinstance(body["results"], list)
        assert len(body["results"]) >= 1
        assert body["results"][0]["title"] == "Compat Test Candidate"


def test_compat_route_unknown_task_404(client):
    for path in _compat_paths("opp-x", str(uuid.uuid4())):
        r = client.get(path, headers=_headers())
        assert r.status_code == 404
        assert r.json()["error"]["status"] == 404  # our error envelope


async def test_compat_route_canonical_unchanged(client):
    task_id = client.portal.call(_seed_completed_task_with_results)
    r = client.get(f"/api/v1/tasks/{task_id}/results", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["task_id"] == task_id
