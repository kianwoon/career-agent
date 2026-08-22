# Career Agent Phase 1

Cloud-hosted career agent: job search, candidate search, matching, persistent
browser sessions, human takeover, and approval controls.

## Stack

- **Frontend**: Next.js + TypeScript (`frontend/`)
- **API**: FastAPI + Pydantic (`backend/`)
- **Workflow**: LangGraph supervisor (`backend/app/agent/`)
- **Browser runtime**: Playwright (local) / Steel (cloud) (`backend/app/services/browser.py`)
- **Storage**: PostgreSQL + Redis + Qdrant (`infra/docker-compose.yml`)

## Quick Start

### 1. Infrastructure (Postgres, Redis, Qdrant)

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 2. Backend

```bash
cd backend
uv sync                 # install deps (Python 3.11+)
cp .env.example .env
python -m app.seed      # create tables + demo user
uv run uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm run dev             # http://localhost:3000
```

## Docker / Deployment

Production Dockerfiles are included:

```bash
# Backend API (includes headless Chromium for LinkedIn automation)
docker build -t career-api -f backend/Dockerfile backend/
docker run -p 8000:8000 -e DATABASE_URL=... -e API_KEYS=... career-api

# Frontend
docker build -t career-web -f frontend/Dockerfile frontend/
docker run -p 3000:3000 career-web
```

- **Postgres**: reuse an existing instance; the API auto-creates tables on boot.
- **Koyeb**: see [`DEPLOY.md`](DEPLOY.md) for full instructions + `koyeb.yaml`.
- **Browser session**: in production there is no local Brave — the bot uses
  captured-session replay (capture once while signed in, replay headlessly).

## Phase 1 API Sketch

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/search/jobs` | Start job search |
| POST | `/api/v1/search/candidates` | Start candidate search |
| GET | `/api/v1/tasks/{id}` | Task status |
| GET | `/api/v1/tasks/{id}/results` | Task results |
| POST | `/api/v1/browser/sessions` | Create browser session |
| POST | `/api/v1/browser/{id}/takeover` | Human takeover |
| POST | `/api/v1/approvals/{id}` | Approve/reject action |
| GET | `/api/v1/health` | Health check (public, no auth) |

## External API Usage

The API is designed for other systems to call. Interactive docs: `/docs`
(Swagger UI) and `/openapi.json` (machine-readable OpenAPI spec).

### Authentication

Send your API key in the `X-API-Key` header:

```bash
curl -X POST http://localhost:8000/api/v1/search/jobs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key-here" \
  -d '{"query": "AI platform leadership", "location": "Singapore"}'
```

Keys are configured in `backend/.env`:

```env
# Comma-separated; optional ":N" = per-key requests/minute
API_KEYS="key1:60,key2:30"
API_RATE_LIMIT_PER_MIN=30
```

- Leave `API_KEYS` empty to disable auth (dev only).
- `/api/v1/health`, `/docs`, `/openapi.json` are public (no key).
- Exceeding a key's rate limit returns `429` with a `Retry-After` header.

### Error format

All errors use a unified envelope:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Missing X-API-Key header",
    "status": 401,
    "details": null,
    "request_id": "b3d552df",
    "timestamp": "2026-08-22T09:49:21Z"
  }
}
```

Common codes: `unauthorized` (401), `rate_limited` (429), `not_found` (404),
`validation_error` (422), `internal_error` (500).

### Long-running tasks

Search tasks run in the background (browser automation + LLM reranking takes
tens of seconds). The pattern for callers:

1. `POST /api/v1/search/jobs` → returns `task_id` + `status`
2. `GET /api/v1/tasks/{task_id}` → poll until `status` is `completed` or `failed`
3. `GET /api/v1/tasks/{task_id}/results` → ranked results with evidence

### CORS

Web clients must be in `CORS_ORIGINS` (JSON array in `backend/.env`). Add the
origins of any web app that calls the API.

## Design

See `career-agent-phase1-design-spec.md` for the full Phase 1 specification.

## API Documentation

See [`docs/API.md`](docs/API.md) for the full integration guide (auth,
endpoints, schemas, errors, worked examples for humans and AI agents).
Live OpenAPI spec: `GET /openapi.json`, Swagger UI: `GET /docs`.
