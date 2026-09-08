# Expired source session UX (pause + re-login banner)

- **Date**: 2026-08-28T03:45:07+0800
- **Type**: decision

## Context

Job-site sessions (cookies/QR logins) expire unpredictably. Final design: during a search, if any custom source's session is detected expired (login-wall probe in execute_flow), the whole search hard-pauses (needs_human -> TaskStatus.paused) even if other sources succeeded — partial silent results are worse than a clear pause. Frontend shows an orange 'Re-login required' banner with a button that expands the source setup panel; user re-logins (password/MFA/QR via the screenshot wizard) and re-runs the search.

## Decision / rationale

Flow: source_flows._looks_logged_out -> nodes._search_custom_sources issues -> run_search early-return pause -> routes persist source_issues in SearchTask.error JSON -> frontend reloginNeeded banner. Lesson: silent partial results from authenticated sources are a bug, not a feature.
