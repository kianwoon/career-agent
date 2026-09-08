# Provider idle stall guard is default-on

- **Date**: 2026-08-30T18:48:28+0800
- **Type**: decision

## Context

Provider streams could stall silently forever when no timeout was configured (the old whole-request AbortSignal.timeout was deliberately removed because it killed healthy long reasoning turns). Observed: z.ai GLM gateway parked a session 15 minutes with zero chunks and zero log lines.

## Decision / rationale

Idle guards are now DEFAULT-ON at 300s via resolveIdleTimeouts (provider.ts) for BOTH phases, but strictly idle-based: they fire only on missing headers or gaps between SSE chunks, never on total request duration — so long healthy reasoning turns cannot be killed. Stalls raise ChunkStallError -> retryable APIError (bounded by existing 5-attempt retry schedule) and log WARN with stallIdleMs. Escape hatches: timeout:false (both phases), chunkTimeout:false, headerTimeout:false. console.warn does not reach opencode.log; only Effect loggers do.
