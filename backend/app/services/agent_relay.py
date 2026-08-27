"""Browser-extension agent relay.

The cloud API is the brain; a browser extension running in the END USER's
browser is the hands. The extension opens an outbound WebSocket to this
relay (no NAT/tunnel needed) and executes navigation/form/extraction
commands in the user's real, logged-in browser — so sites like LinkedIn see
a genuine browser and never block.

Design:
- One connected agent (single-user deployment). New connections replace old.
- `dispatch` queues a command and awaits the agent's response with a timeout.
- API endpoints call `agent_registry.dispatch(...)` to run browser actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_S = 120.0


@dataclass
class AgentConnection:
    """One connected extension + its pending command futures."""

    ws: WebSocket
    connected_at: float = field(default_factory=time.time)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(payload))


class AgentRegistry:
    """Tracks the connected extension agent and routes commands to it."""

    def __init__(self) -> None:
        self.agent: AgentConnection | None = None
        self._lock = asyncio.Lock()
        self._cond: asyncio.Condition = asyncio.Condition()

    # -- connection lifecycle (called by the WS endpoint) ----------------

    async def connect(self, ws: WebSocket) -> AgentConnection:
        await ws.accept()
        async with self._lock:
            old = self.agent
            if old is not None:
                # Single-agent deployment: newest connection wins.
                try:
                    await old.ws.close(code=4000, reason="Replaced by a new agent connection")
                except Exception:
                    pass
            self.agent = AgentConnection(ws=ws)
            async with self._cond:
                self._cond.notify_all()
            logger.info("Agent connected (replacing=%s)", old is not None)
            return self.agent

    async def disconnect(self, conn: AgentConnection) -> None:
        async with self._lock:
            if self.agent is conn:
                self.agent = None
                logger.info("Agent disconnected")
        # Fail anything still pending on this connection.
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Agent disconnected"))
        conn.pending.clear()
        async with self._cond:
            self._cond.notify_all()

    # -- status -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.agent is not None

    async def wait_for_agent(self, timeout_s: float = 20.0) -> AgentConnection:
        """Wait until an agent is (re)connected, or raise."""
        deadline = time.monotonic() + timeout_s
        while not self.connected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "No browser agent connected — open the Career Agent extension in your browser"
                )
            async with self._cond:
                await asyncio.wait_for(self._cond.wait(), timeout=remaining)
        assert self.agent is not None
        return self.agent

    # -- command dispatch --------------------------------------------------

    async def dispatch(
        self, action: str, params: dict[str, Any], timeout_s: float = COMMAND_TIMEOUT_S
    ) -> Any:
        """Send a command to the agent and await its result.

        Actions: navigate | fill | click | press | extract | screenshot_hint
        The extension replies with {"ok": ..., "data"/"error", cmd id}.
        """
        conn = await self.wait_for_agent()
        cmd_id = f"cmd-{time.time_ns()}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        conn.pending[cmd_id] = fut
        try:
            await conn.send({"id": cmd_id, "action": action, "params": params})
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            raise RuntimeError(f"Agent command '{action}' timed out after {timeout_s:.0f}s")
        finally:
            conn.pending.pop(cmd_id, None)

    # -- inbound messages (called by the WS endpoint) -----------------------

    async def handle_message(self, conn: AgentConnection, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Agent sent non-JSON message: %.80s", raw)
            return
        cmd_id = msg.get("id")
        if cmd_id and cmd_id in conn.pending:
            fut = conn.pending[cmd_id]
            if not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("data"))
                else:
                    fut.set_exception(RuntimeError(msg.get("error") or "Agent command failed"))
        else:
            logger.info("Agent note: %.120s", msg)


agent_registry = AgentRegistry()


async def agent_ws_endpoint(ws: WebSocket) -> None:
    """FastAPI WebSocket route: /api/v1/agent/ws"""
    conn = await agent_registry.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            await agent_registry.handle_message(conn, raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # unexpected — log and clean up
        logger.warning("Agent WS error: %s", exc)
    finally:
        await agent_registry.disconnect(conn)
