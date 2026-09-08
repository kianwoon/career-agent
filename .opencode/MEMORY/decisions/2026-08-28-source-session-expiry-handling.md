# Source session expiry handling

- **Date**: 2026-08-28T01:32:53+0800
- **Type**: decision

## Context

Job sites expire cookies unpredictably, breaking recorded flows silently. Design: execute_flow runs a logged-out check (login URL/title/body patterns, off-domain redirect) after landing and mid-flow; expired sessions mark the SourceFlow 'broken', return a 'Session expired' human_reason, and the API surfaces source_issues[] on GET /tasks/{id}/results so the frontend can instruct the user to press the source's Login button again.

## Decision / rationale

Pipeline: services/source_flows.py::_looks_logged_out -> agent/nodes.py::_search_custom_sources (issues + flow.status=broken) -> routes/routes.py (persist JSON in SearchTask.error) -> frontend page.tsx (warn event with re-login instruction). SearchTask.error doubles as JSON store for source_issues — parse defensively.
