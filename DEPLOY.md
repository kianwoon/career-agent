# Deploying Career Agent to Koyeb

This guide deploys the Career Agent to [Koyeb](https://www.koyeb.com) using
the provided Dockerfiles and `koyeb.yaml`. It **reuses your existing Koyeb
Postgres** — no new database needed.

---

## 1. What you're deploying

| Service | Image source | Port | Purpose |
|---------|-------------|------|---------|
| `career-api` | `backend/Dockerfile` | 8000 | FastAPI + LangGraph + Playwright (headless Chromium) |
| `career-web` | `frontend/Dockerfile` | 3000 | Next.js UI |

Existing Koyeb Postgres is reused — the API **auto-creates tables on boot**
(no manual migration step).

---

## 2. Prerequisites

- [Koyeb CLI](https://www.koyeb.com/docs/cli/installation) (`koyeb`) or the dashboard
- Your existing Koyeb Postgres connection string (`DATABASE_URL`)
- An API key for the bot (generate: `python3 -c "import secrets; print(secrets.token_hex(16))"`)
- A session encryption key (generate: `python3 -c "import secrets; print(secrets.token_hex(32))"`)
- Your GLM coding-plan key (`LLM_API_KEY`) if enabling LLM reranking

---

## 3. Option A — Dashboard (recommended for first deploy)

### 3.1 API service

1. **Create Service** → **GitHub** → select `kianwoon/career-agent`
2. Branch `main`; **Build** → **Dockerfile**
   - **Docker context**: `/backend`
   - **Dockerfile**: `/backend/Dockerfile`
3. **Port**: `8000`
4. **Instance**: `nano` (512MB) minimum; **2GB recommended** (Chromium)
5. **Scaling**: `min=1, max=1`
6. **Environment variables** (set all):

   | Var | Value |
   |-----|-------|
   | `DATABASE_URL` | `postgresql+asyncpg://USER:PASS@HOST:5432/career_agent` ← your Koyeb Postgres |
   | `API_KEYS` | `mykey1:30,mykey2:60` |
   | `SESSION_ENCRYPTION_KEY` | 64-hex-char key |
   | `LLM_ENABLED` | `true` |
   | `LLM_API_KEY` | your GLM key |
   | `LLM_BASE_URL` | `https://api.z.ai/api/anthropic/v1/messages` |
   | `LLM_MODEL_NAME` | `GLM-5.3` |
   | `CORS_ORIGINS` | `["https://career-web-<org>.koyeb.app"]` |
   | `BRAVE_CDP_URL` | *(leave empty — production uses captured-session replay)* |

7. Deploy. Note the public URL, e.g. `https://career-api-<org>.koyeb.app`.

### 3.2 Frontend service

1. **Create Service** → **GitHub** → same repo
2. Build → Dockerfile: context `/frontend`, Dockerfile `/frontend/Dockerfile`
3. **Port**: `3000`
4. **Build args**:
   - `NEXT_PUBLIC_API_BASE_URL=https://career-api-<org>.koyeb.app`
   - `NEXT_PUBLIC_API_KEY=<one of your API_KEYS>`
5. Deploy. URL: `https://career-web-<org>.koyeb.app`

---

## 4. Option B — CLI (koyeb.yaml)

Edit `koyeb.yaml` with your real values, then:

```bash
koyeb service create career-api --git github.com/kianwoon/career-agent \
  --git-branch main \
  --git-build-context /backend \
  --git-dockerfile /backend/Dockerfile \
  --port 8000

koyeb service create career-web --git github.com/kianwoon/career-agent \
  --git-branch main \
  --git-build-context /frontend \
  --git-dockerfile /frontend/Dockerfile \
  --port 3000
```

Then set env vars on each service in the dashboard (or `koyeb service update`).

> Note: `koyeb.yaml` in this repo is a reference template — Koyeb's exact
> schema may differ slightly by plan. The dashboard flow above is the
> most reliable path.

---

## 5. Critical: browser session (LinkedIn login)

In production there is **no local Brave**. The bot relies on **captured
session replay**:

1. Deploy the API.
2. In the UI (or via API), run **Capture** while your real browser is signed
   in and reachable (e.g. from your laptop over a VPN/tunnel to a CDP port,
   or a one-time capture step).
3. The session is encrypted and stored in Postgres.
4. All subsequent searches **replay the stored session** in headless Chromium
   — no Brave needed on the server.

If cookies expire, the bot returns `needs_human` → re-capture.

> **Security**: `SESSION_ENCRYPTION_KEY` protects the stored cookies at rest.
> Keep it secret (Koyeb secret or env var, never in git).

---

## 6. Sizing reference (measured locally)

| Component | Memory | Notes |
|-----------|--------|-------|
| Python API (uvicorn) | ~165 MB | FastAPI + LangGraph |
| Headless Chromium | ~690 MB | per active browser session |
| Koyeb Postgres | — | **reused**, not provisioned here |
| Frontend (Next.js) | ~100 MB | static/standalone |

**Recommendation**: `career-api` on a **2GB** instance (`nano`/`micro` is too
small once Chromium launches). `career-web` on `nano` (512MB) is fine.

---

## 7. After deploy

- [ ] `/api/v1/health` returns `{"status":"ok"}`
- [ ] API docs at `/docs`
- [ ] `GET /api/v1/tasks/{id}` works with your API key
- [ ] Capture + Replay a session, then run a search
- [ ] Frontend loads and shows ranked results

---

## 8. Env var reference (backend)

| Var | Required | Notes |
|-----|----------|-------|
| `DATABASE_URL` | ✅ | Asyncpg URL to your Koyeb Postgres |
| `API_KEYS` | ⚠️ | Empty = no auth (dev only) |
| `SESSION_ENCRYPTION_KEY` | ✅ prod | 64 hex chars, AES-256 |
| `LLM_ENABLED` / `LLM_API_KEY` | optional | GLM reranking |
| `CORS_ORIGINS` | ✅ | Frontend URL |
| `API_RATE_LIMIT_PER_MIN` | optional | default 30 |
| `PACING_*` | optional | tuning knobs |
