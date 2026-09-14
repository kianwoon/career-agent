# Unguarded late-pass dispatch turns a shrunken budget into a fatal pause

**Date**: 2026-09-14
**Area**: backend/app/services/linkedin_people.py :: search_linkedin_people (extension branch)
**Symptom (live Koyeb)**: task paused with
"Candidate search failed: Agent command 'linkedin_people_plan' timed out after 33s (is the browser open?)"

## Root cause
_pass_timeout(n) = max(5, min(max(180, 90*n), remaining_budget)). A 33s timeout
means only 33s of the 480s SEARCH_BUDGET_S remained when a widen pass fired.
The relaxed-pass dispatch had NO try/except (unlike the throttle-retry and
enrich top-up, which both degrade gracefully). A LinkedIn plan dispatch can
NEVER finish in 33s — the extension sleeps 6-11s BEFORE EACH query, then
navigates and extracts — so the dispatch was a guaranteed timeout whose
RuntimeError propagated to run_search's catch-all (nodes.py ~1072), which sets
needs_human=True -> task PAUSES with a reason that blames the browser.

## Fix
- MIN_PASS_TIMEOUT_S = 90.0: skip relaxed/broad passes when the pass timeout is
  below this (guaranteed timeout otherwise); note in plan_detail.
- Guard both dispatches with try/except: swallow, log, append
  "+ relaxed failed: ..." / "+ broad failed: ..." to plan_detail, keep pass-1
  rows. needs_human/human_reason NEVER set by widen passes.
Contract: widen passes may only ADD results, never fail the task.

## Diagnosis lesson
"timed out after 33s" is arithmetic, not a browser problem: 33 = remaining
budget at dispatch time. Whenever a caller-supplied timeout_s produces a weird
number, compute who could have produced it: min(floor, remaining) — then ask
what consumed the budget before it. The primary pass (180s floor) + throttle
retry (+45s sleep) consumed ~447s of the 480s budget, leaving 33s for relaxed.

## Harness notes
- Patch target for dispatch fakes: app.services.agent_relay.agent_registry
  (imported inside the function, so patch the attribute on the shared registry
  object, not a local name).
- Drive the budget via search_linkedin_people(deadline=monotonic()+N) — no env
  var needed.
- Local full suite needs CI-style DATABASE_URL (see .github/workflows/ci.yml);
  without postgres the DB modules fail at collection (psycopg2 async-driver
  error, environment-only). Known-good baseline with CI env: 8 failed/153
  passed — the 8 are pre-existing (compat_routes x3, test_graph, security x2,
  sources_api x2).
