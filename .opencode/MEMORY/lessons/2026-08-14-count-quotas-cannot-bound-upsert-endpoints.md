# COUNT(*) quotas cannot bound upsert endpoints

- **Date**: 2026-08-14T21:01:12+0800
- **Type**: lesson

## What happened

Shipped INTELLIGENCE_DAILY_QUOTA as a COUNT(*) of rows created since midnight, mirroring the CV upload quota. But the intelligence POSTs are UPSERTS (one row per entity): re-runs UPDATE the row, created_at never moves, and my first fix attempt (stamping last_queued_at per POST) still failed because one row stamped N times is still one row — a COUNT() of rows cannot count events that create no rows. My own regression test caught it: [202,202,202,202,202] instead of [202,202,202,429,429].

## Root cause / fix

When bounding spend on an upsert endpoint, use a counter incremented atomically per request (one UPDATE..RETURNING on the scoped parent — e.g. tenants.llm_runs_date/count with date rollover in the same statement), never a row count. Always write the adversarial test FIRST (loop the same entity N times, assert the N+1th is refused) — it is the only test that distinguishes counting rows from counting runs.
