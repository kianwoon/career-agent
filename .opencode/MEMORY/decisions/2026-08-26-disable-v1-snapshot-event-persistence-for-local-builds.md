# Disable V1 snapshot event persistence for local builds

- **Date**: 2026-08-26T20:29:49+0800
- **Type**: decision

## Context

commitDurableEvent in packages/core/src/event.ts runs projectors inside the same transaction that inserts into ; gating only the INSERT leaves projections intact. Local-only users never read V1 snapshot rows back — app history reads message API, V2 history reads session.next.* via readAggregate with type filtering.

## Decision / rationale

For this machine (no workspace sync), keep OPENCODE_DISABLE_V1_EVENT_LOG=true baked into desktop sidecar env. This is a local divergence from upstream; revisit if experimental workspaces/sync are ever enabled.
