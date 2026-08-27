"""API routes for the browser-extension agent.

- GET  /agent/status        — is the extension connected?
- POST /agent/execute       — run a flow (steps) in the user's browser
- GET  /agent/results       — (results are POSTed by the extension; see tasks)
"""

from __future__ import annotations

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


class FlowExecuteRequest(BaseModel):
    """Run a recorded-style flow in the user's browser via the extension.

    Steps use the same schema as recorded flows:
      {"action": "navigate", "url": "..."}
      {"action": "fill", "selector": "...", "value": "...", "param": "query"?}
      {"action": "click", "selector": "..."}
      {"action": "press", "key": "Enter"}
      {"action": "extract", "card": "css", "fields": {...}?}
    """

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
                import asyncio

                await asyncio.sleep(float(step.get("seconds", 2)))
            elif action == "extract":
                data = await agent_registry.dispatch(
                    "extract",
                    {
                        "card": step.get("card", ""),
                        "fields": step.get("fields", {}),
                        "maxItems": 30,
                    },
                    timeout_s=60,
                )
                results = data if isinstance(data, list) else []
            else:
                logger.warning("Unknown flow step action: %s", action)
    except RuntimeError as exc:
        return {"results": [], "needs_human": False, "error": str(exc)}

    return {
        "results": results,
        "count": len(results),
        "needs_human": False,
        "error": None,
    }
