# Brave CDP wedges after repeated Playwright connect_over_cdp cycles (each browser-level attach re-enumerates all targets; endpoint degrades ~10-15 min after launch; ws handshake times out even at 180s). FIX: hold ONE connection per plan — career bot linkedin_people.py _PlanSession (lazy connect thunk = _connect_with_best_session; reuse the SAME page it returns; the pair is (playwright_manager, page) and the manager has NO .contexts). Set connect timeout 15s fail-fast. Also: pkill -f 'uvicorn app.main' before backend restart or stale code serves; results endpoint is GET /api/v1/tasks/{id}/results (no /search prefix). E2E verified: task 8f6d1074 completed 210s, 9 candidates, plan_detail 'query3: 10 -> 9 unique'.

- **Date**: 2026-09-01T15:41:48+0800
- **Type**: lesson

## What happened



## Root cause / fix


