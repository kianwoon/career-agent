"""API routes for the polling browser-extension agent.

- GET  /agent/status   — has the extension polled recently?
- GET  /agent/poll     — extension: fetch next command (long-ish poll)
- POST /agent/result   — extension: post command result
- POST /agent/execute  — run a flow via the agent (used by API/tests)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.agent_relay import agent_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent")


class AgentStatus(BaseModel):
    connected: bool


@router.get("/status", response_model=AgentStatus)
async def agent_status() -> AgentStatus:
    return AgentStatus(connected=agent_registry.connected)


@router.get("/poll", dependencies=[])
async def agent_poll(wait: int = 0, boot: str | None = None) -> dict[str, Any]:
    """Extension fetches the next command. Optional `wait` seconds long-poll.

    `boot` identifies the extension worker instance: when it changes (worker
    reloaded mid-command), all pending commands are orphaned — their caller
    can never be answered by the new instance — so they are failed
    immediately instead of burning their full timeout.
    """
    if boot is not None:
        agent_registry.note_boot(boot)
    # NOTE: poll() CLAIMS the returned command (deliver-once, so duplicate
    # extension loops can't double-execute). Every call must use its return
    # value — a throwaway poll() call claims a command and drops it, and the
    # next call finds nothing (this silently starved ALL dispatches).
    cmd = None
    if wait > 0:
        deadline = asyncio.get_event_loop().time() + min(wait, 25)
        while True:
            cmd = agent_registry.poll()
            if cmd is not None:
                break
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.5)
    else:
        # Single poll refreshes liveness and fetches at once.
        cmd = agent_registry.poll()

    if cmd is None:
        return {"command": None}
    return {"command": {"id": cmd.id, "action": cmd.action, "params": cmd.params}}


class AgentResult(BaseModel):
    id: str = Field(..., min_length=1)
    ok: bool
    data: Any | None = None
    error: str | None = None


@router.post("/result")
async def agent_result(req: AgentResult) -> dict[str, bool]:
    handled = agent_registry.resolve(req.id, req.ok, req.data, req.error)
    return {"ok": True, "handled": handled}


class FlowExecuteRequest(BaseModel):
    base_url: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    steps: list[dict[str, Any]] = Field(..., min_items=1)


@router.post("/execute")
async def agent_execute(req: FlowExecuteRequest) -> dict[str, Any]:
    """Dispatch flow steps to the extension; return extracted results."""
    results: list[dict[str, Any]] = []
    try:
        for step in req.steps:
            action = step.get("action")
            if action == "navigate":
                await agent_registry.dispatch("navigate", {"url": step["url"]})
            elif action == "fill":
                value = req.query if step.get("param") == "query" else step.get("value", "")
                await agent_registry.dispatch(
                    "fill", {"selector": step["selector"], "text": value}
                )
            elif action == "click":
                await agent_registry.dispatch("click", {"selector": step["selector"]})
            elif action == "press":
                await agent_registry.dispatch("press", {"key": step.get("key", "Enter")})
            elif action == "wait":
                await asyncio.sleep(float(step.get("seconds", 2)))
            elif action == "extract":
                data = await agent_registry.dispatch(
                    "extract",
                    {"card": step.get("card", ""), "fields": step.get("fields", {}), "maxItems": 30},
                    timeout_s=60,
                )
                results = data if isinstance(data, list) else []
            elif action == "find_result_card":
                data = await agent_registry.dispatch("find_result_card", {}, timeout_s=30)
                results = [data] if isinstance(data, dict) else []
    except RuntimeError as exc:
        return {"results": [], "needs_human": False, "error": str(exc)}

    return {"results": results, "count": len(results), "needs_human": False, "error": None}
