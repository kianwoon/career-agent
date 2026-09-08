# Default idle stall guard tuned to 180s

- **Date**: 2026-08-30T19:39:50+0800
- **Type**: decision

## Context

Initial default-on idle guard shipped at 300s. User judged 3 minutes of total silence is already a wedged stream and asked for 180s.

## Decision / rationale

DEFAULT_IDLE_TIMEOUT is now 180_000 (provider.ts). Semantics unchanged: idle-only guard on headers + inter-chunk gaps, never total request duration; per-provider escape hatches still timeout:false / chunkTimeout:false / headerTimeout:false; explicit values still win. Regenerated SDK + openapi.json, rebuilt prod, verify-prod gate passed (node-CLa5NcRN.js).
