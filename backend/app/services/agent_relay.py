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

# MV3 Chrome can only fire alarms every >=30s, so a suspended worker that
# wakes on its alarm polls at best every ~30s. 90s covers that cadence plus
# a busy worker executing a long tab command (which used to starve /poll
# and made the very next search leg think the agent was gone).
CONNECTED_WINDOW_S = 90.0


@dataclass
class Command:
    id: str
    action: str
    params: dict[str, Any]
    enqueued_at: float = field(default_factory=time.time)
    result: Any | None = None
    error: str | None = None
    done: bool = False
    claimed: bool = False  # handed to the extension; never re-offer while claimed
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class AgentRegistry:
    """Command queue + result store for the polling extension agent."""

    def __init__(self) -> None:
        self.pending: list[Command] = []
        self._seen_ids: set[str] = set()
        self.last_poll_ts: float | None = None  # liveness signal for /status
        self.boot_id: str | None = None  # extension worker instance id
        # All commands share ONE browser tab, so concurrent dispatches
        # (e.g. LinkedIn plan + jobstreet flow in asyncio.gather) execute
        # back-to-back in the tab while each caller's timeout keeps ticking
        # — queued callers time out even though their command never ran.
        # Serialize dispatch+await so timeouts measure execution, not queue
        # wait.
        self._exec_lock = asyncio.Lock()

    def note_boot(self, boot_id: str) -> None:
        """Record the polling worker instance; fail orphaned commands.

        A worker reload mid-command orphans every pending command — the new
        instance can never answer them, so they are failed NOW (callers get
        a fast soft-miss) instead of burning their full dispatch timeout.
        """
        if self.boot_id is not None and self.boot_id != boot_id and self.pending:
            for cmd in self.pending:
                if not cmd.done and cmd.future and not cmd.future.done():
                    cmd.future.set_exception(
                        RuntimeError("Agent restarted — command orphaned by extension reload")
                    )
                    cmd.future.exception()  # consume to avoid "never retrieved" warnings
            self.pending = [c for c in self.pending if c.done]
        self.boot_id = boot_id

    # -- API-side dispatch --------------------------------------------------

    @property
    def connected(self) -> bool:
        """The agent is 'connected' if it polled recently (within 40s)."""
        return self.last_poll_ts is not None and (time.time() - self.last_poll_ts) < CONNECTED_WINDOW_S

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
        self, action: str, params: dict[str, Any], timeout_s: float = COMMAND_TIMEOUT_S,
        lock_wait_s: float = 60.0,
    ) -> Any:
        """Enqueue a command and await the extension's result.

        lock_wait_s bounds the wait for the shared-tab lock: when a previous
        holder died (extension reload mid-command), an unbounded wait let
        callers stack 420s timeouts serially — a 12-minute wall-clock hang.
        """
        await self.wait_for_agent()
        try:
            await asyncio.wait_for(self._exec_lock.acquire(), timeout=lock_wait_s)
        except TimeoutError:
            raise RuntimeError(
                f"Agent busy — dispatch lock not released within {lock_wait_s:.0f}s "
                "(a previous command is stuck or the extension reloaded mid-run)"
            )
        locked = True
        try:
            cmd = Command(id=f"cmd-{uuid.uuid4().hex[:12]}", action=action, params=params)
            self.pending.append(cmd)
            try:
                return await asyncio.wait_for(cmd.future, timeout=timeout_s)
            except TimeoutError:
                self.pending = [c for c in self.pending if c.id != cmd.id]
                raise RuntimeError(
                    f"Agent command '{action}' timed out after {timeout_s:.0f}s (is the browser open?)"
                )
            except RuntimeError as exc:
                if "agent busy" in str(exc).lower():
                    # Extension rejected the command because a STALE busy flag
                    # (leaked promise — cleared by the extension-side watchdog
                    # within ~4 min) or a duplicate poll loop is holding the
                    # tab. Do NOT stack retries: each one leaves an orphan in
                    # pending that poll() would hand to the extension again —
                    # the previous retry chain shrank 120s→5s and 504'd the
                    # request (live 2026-09-04). Fail fast so the UI reports
                    # "agent busy — wait and press Record again" immediately.
                    self.pending = [c for c in self.pending if c.id != cmd.id]
                    raise RuntimeError(
                        "Agent is busy finishing a previous command — "
                        "wait ~10s and press Record again"
                    )
                raise
            except BaseException:
                # Cancellation (watchdog deadline, shutdown) must not leave
                # the abandoned command in the queue — poll() would keep
                # handing it to the extension, which would re-execute it on
                # the shared tab mid-other-task.
                self.pending = [c for c in self.pending if c.id != cmd.id]
                raise
        finally:
            if locked:
                self._exec_lock.release()

    # -- extension-side poll/result -----------------------------------------

    def poll(self) -> Command | None:
        """Extension asks for the next command (oldest first)."""
        self.last_poll_ts = time.time()
        if not self.pending:
            return None
        # Skip stale commands nobody will answer (age > COMMAND_TIMEOUT_S,
        # done or not — an abandoned not-done command must never be handed
        # to the extension for re-execution); return the oldest live one.
        now = time.time()
        self.pending = [c for c in self.pending if now - c.enqueued_at < COMMAND_TIMEOUT_S]
        for c in self.pending:
            if not c.done and not c.claimed:
                c.claimed = True  # deliver ONCE — a duplicate loop must
                return c          # never get the same command twice
        return None

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
