# Career-agent flow recording: SEEK/jobstreet selectors

**Date:** 2026-09-04 · **Repo:** `~/Downloads/career bot` (deployed as Koyeb `career-agent`)

## Symptom
External candidate searches for SEEK (`sg.employer.seek.com`) returned junk or paused. User said "the script is totally wrong".

## Root cause
The recorded flow in `source_flows.steps` (Postgres) had captured garbage selectors:
`{"card": "div#app", "fields": {"title": "a.pswd5d0"}}` — the page root as one card, plus an obfuscated class SEEK rotates per deploy. Symptom of recording by hand-clicking instead of the extension recorder or LLM auto-discovery.

## Diagnostics that worked (in order)
1. **Live DOM check via AppleScript** into the user's Brave tab (`osascript … execute javascript`) — `document.querySelectorAll("[data-testid]")` counts immediately reveal the stable hooks. SEEK talent search renders `[data-testid=profile-card]` (20/page), `[data-testid=work-history]`, button ids like `accessProfile-<candidateId>`, nav `[aria-label="Pagination of results"]`. **No `<a>` inside cards.**
2. **Get the career-agent DB URL from Koyeb**, not from repo `.env` (which points at localhost):
   `koyeb deployments list --service 3c00f445 -o json` → deployment id → `koyeb deployment get <id> -o json` → `.env[] | select(.key=="DATABASE_URL")`. The service list shows career services: `career-api` = `3c00f445`, `career-web` = `09e7147d`.
3. Dump the flow: `SELECT jsonb_pretty(steps::jsonb) FROM source_flows sf JOIN sources s ON s.id=sf.source_id WHERE s.name='jobstreet - candidate';` (steps column is `json`, so cast to `jsonb`).

## Fix
UPDATE the `steps` json with real selectors: card `[data-testid=profile-card]`, summary `[data-testid=work-history]`, empty title (the normalizer `_normalize_flow_candidate` in `backend/app/agent/nodes.py` falls back to first line of raw_text).

## Verification
User ran a fresh search — "auto discover flow work flawlessly". Prefer the extension recorder / LLM auto-discovery over hand-recorded flows: their `buildSelector` prefers `#id` → `[data-testid]` → aria-label, so they survive SEEK's class rotation.

## Known residual (career-agent repo, unfixed)
- Server executor dedupes pagination results by `url` (`source_flows.py` ~748); SEEK cards have no hrefs → all `url:""` → pages 2+ may be dropped.
- `cmdFindResultCard` fallback heuristic (`extension/background.js` ~918) requires `a[href]` per card → skips valid SEEK cards.
