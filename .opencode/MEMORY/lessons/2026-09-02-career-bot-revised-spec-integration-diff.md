# Career-bot revised spec (2026-09-02): integration diff checklist

When the career bot ships a revised `docs/CANDIDATES-API.md`, don't diff the
doc in your head — probe the **live** service, because the doc, the OpenAPI
schema, and what actually answers can all differ.

## The path trap (cost ~20 min on 2026-09-02)

The career bot's FastAPI router declares routes WITHOUT a prefix
(`/search/candidates`, `/tasks/{id}`), then `main.py` mounts the SAME router
under several prefixes (`/api/v1`, `/v1`, and bare). On top of that, Koyeb's
route rule `/api -> career-api:8000` **strips the leading `/api`** before
forwarding. Net effect, verified live:

- Expressautomate sets `CAREER_BOT_URL=https://career-agent-<hash>.koyeb.app/api`
- Client paths are `/api/v1/search/candidates`, `/api/v1/tasks/{id}`,
  `/api/v1/tasks/{id}/results` — these ARE the live, working URLs
- `/api/openapi.json` (no auth login wall, unlike `/openapi.json` which 307s
  to `/login`) lists paths WITH `/api/v1/...` embedded in them, which is how
  you can tell the prefix situation apart
- Probe with an empty body `{}` before assuming a 404 means "endpoint gone":
  a live route answers 422 `validation_error`; a wrong path answers 404
  `not_found`. POSTing real search payloads during a diff wastes a search.

## What the revised spec changed (2026-09-02)

- `POST /api/v1/search/candidates` (renamed from `POST /api/v1/tasks`); body
  prefers `platforms: []` (list, cap 5, silently truncated — oversized input
  is NEVER a 422) over legacy `platform` scalar; `queries` cap 5 same deal.
- Task statuses now include `paused` (human takeover: stop, don't retry) and
  `waiting_approval`.
- Results carry `source_platform` — the display label (`LinkedIn`,
  `JobStreet`) the UI used to hand-roll. `source` stays the raw id.
  `source_url` semantics: LinkedIn = real profile URL; JobStreet/SEEK =
  the platform's SEARCH page (no per-candidate deep links exist) — the
  "Open profile" link opens a search page for those; that is by design.
- New `GET /api/v1/search/platforms` (live ids with active `find_candidates`
  flows) and `GET /api/v1/search/history`; 429 carries `Retry-After`.

## Expressautomate-side conformance state

- `backend/app/services/career_bot.py` already handles the error envelope
  (`error.message` verbatim), 429+Retry-After (`CareerBotRateLimited`), and
  passes `paused` through as its own state.
- `backend/app/api/external_candidates.py::_payload_from_plan` maps plan
  `platform` → `platforms: [value]`.
- Frontend `platformLabel(candidate)` prefers `source_platform`, falls back
  to id-mapping for results persisted before the field existed (results are
  stored verbatim in `external_candidate_searches.results` JSONB).
