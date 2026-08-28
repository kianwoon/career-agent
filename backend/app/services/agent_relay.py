"""Browser-extension agent relay — HTTP polling edition.

The MV3 service worker + WebSocket approach proved unreliable (Brave
suspends idle workers, killing the socket every ~30s). Polling is the
platform-reliable pattern: every HTTP request wakes the worker, so the
extension simply polls for pending commands and posts results back.

Design:
- `dispatch(action, params)` enqueues a command and awaits its result.
- Extension: GET /agent/poll?agent=<id> -> next command (long-ish poll)
             POST /agent/result -> command result
- No persistent connection, nothing to suspend, no reconnect logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_S = 180.0


@dataclass
class Command:
    id: str
    action: str
    params: dict[str, Any]
    enqueued_at: float = field(default_factory=time.time)
    result: Any | None = None
    error: str | None = None
    done: bool = False
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class AgentRegistry:
    """Command queue + result store for the polling extension agent."""

    def __init__(self) -> None:
        self.pending: list[Command] = []
        self._seen_ids: set[str] = set()
        self.last_poll_ts: float | None = None  # liveness signal for /status

    # -- API-side dispatch --------------------------------------------------

    @property
    def connected(self) -> bool:
        """The agent is 'connected' if it polled recently (within 15s)."""
        return self.last_poll_ts is not None and (time.time() - self.last_poll_ts) < 15

    async def wait_for_agent(self, timeout_s: float = 20.0) -> None:
        deadline = time.time() + timeout_s
        while not self.connected:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError(
                    "No browser agent connected — open the Career Agent extension in your browser"
                )
            await asyncio.sleep(0.5)

    async def dispatch(
        self, action: str, params: dict[str, Any], timeout_s: float = COMMAND_TIMEOUT_S
    ) -> Any:
        """Enqueue a command and await the extension's result."""
        await self.wait_for_agent()
        cmd = Command(id=f"cmd-{uuid.uuid4().hex[:12]}", action=action, params=params)
        self.pending.append(cmd)
        try:
            return await asyncio.wait_for(cmd.future, timeout=timeout_s)
        except TimeoutError:
            self.pending = [c for c in self.pending if c.id != cmd.id]
            raise RuntimeError(
                f"Agent command '{action}' timed out after {timeout_s:.0f}s (is the browser open?)"
            )

    # -- extension-side poll/result -----------------------------------------

    def poll(self) -> Command | None:
        """Extension asks for the next command (oldest first)."""
        self.last_poll_ts = time.time()
        if not self.pending:
            return None
        # Skip stale commands nobody will answer; return the oldest live one.
        now = time.time()
        self.pending = [
            c for c in self.pending if now - c.enqueued_at < COMMAND_TIMEOUT_S or not c.done
        ]
        if not self.pending:
            return None
        return self.pending[0]

    def resolve(self, cmd_id: str, ok: bool, data: Any = None, error: str | None = None) -> bool:
        for i, cmd in enumerate(self.pending):
            if cmd.id == cmd_id:
                self.pending.pop(i)
                cmd.done = True
                if ok:
                    if not cmd.future.done():
                        cmd.future.set_result(data)
                else:
                    if not cmd.future.done():
                        cmd.future.set_exception(RuntimeError(error or "Agent command failed"))
                return True
        # Result for an unknown/timed-out command — ignore.
        return False


agent_registry = AgentRegistry()
