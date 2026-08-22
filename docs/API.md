# Career Agent API — Integration Guide

> For humans and AI agents. This document is self-contained: auth, endpoints,
> request/response schemas, error handling, and working examples. The
> machine-readable OpenAPI spec is available live at `/openapi.json`
> (interactive UI at `/docs`).

---

## 1. Quick Facts

| Item | Value |
|------|-------|
| **Base URL (dev)** | `http://localhost:8000` |
| **Protocol** | HTTP/JSON (REST) |
| **Auth** | API key via `X-API-Key` header |
| **OpenAPI spec** | `GET /openapi.json` (public) |
| **Swagger UI** | `GET /docs` (public) |
| **Health check** | `GET /api/v1/health` (public, no key) |
| **Task model** | Async: POST starts a task, poll `GET /tasks/{id}` until done |
| **Version** | v1, prefix `/api/v1` |

---

## 2. Authentication

All endpoints under `/api/v1` **require** an API key, except `/api/v1/health`,
`/docs`, and `/openapi.json`.

Send the key in the `X-API-Key` header:

```bash
curl http://localhost:8000/api/v1/health \
  -H "X-API-Key: YOUR_KEY"
```

Keys are configured server-side in `backend/.env`:

```env
API_KEYS="key1:60,key2:30"     # key1 allows 60 req/min, key2 allows 30
API_RATE_LIMIT_PER_MIN=30      # default when no ":N" suffix
```

- Missing or invalid key → **401** `{"error":{"code":"unauthorized",...}}`
- Rate limit exceeded → **429** with `Retry-After` header

---

## 3. Endpoints Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness check (public) |
| POST | `/api/v1/search/jobs` | Start a job search task |
| POST | `/api/v1/search/candidates` | Start a candidate search task |
| GET | `/api/v1/tasks/{task_id}` | Get task status |
| GET | `/api/v1/tasks/{task_id}/results` | Get ranked results |
| POST | `/api/v1/browser/sessions` | Create a browser session |
| POST | `/api/v1/browser/{session_id}/takeover` | Human takeover control |
| GET | `/api/v1/browser/{session_id}/observe` | Observe current browser page |
| POST | `/api/v1/approvals/{approval_id}` | Approve/reject a pending action |
| GET | `/` | Service info |

---

## 4. Core Flow: Search → Poll → Results

Search tasks run **asynchronously in the background** (browser automation + LLM
reranking can take 30–120s). The POST returns immediately with a `task_id`;
callers then poll for completion:

1. **POST** a search → get `task_id` (returns in <1s, `status: pending`)
2. **Poll** `GET /tasks/{task_id}` until `status` is `completed` or `failed`
3. **Fetch** `GET /tasks/{task_id}/results`

### 4.1 Start a Job Search

**`POST /api/v1/search/jobs`**

Request body:

```json
{
  "query": "AI platform leadership roles in Singapore",
  "location": "Singapore"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | ✅ | Natural-language job search request |
| `location` | string | ❌ | Location filter, e.g. "Singapore" |

Response **201**:

```json
{
  "task_id": "3b3a1cec-d262-4b69-a2fc-832e6f6a392c",
  "type": "jobs",
  "status": "pending",
  "workflow_state": null,
  "created_at": "2026-08-22T09:00:00Z",
  "started_at": null,
  "completed_at": null,
  "error": null
}
```

### 4.2 Start a Candidate Search

**`POST /api/v1/search/candidates`**

Request body:

```json
{
  "query": "Java, Kafka, payments, microservices, banking experience",
  "location": "Singapore"
}
```

The `query` is the candidate criteria (skills, domain, seniority). Same
response shape as jobs, with `"type": "candidates"`.

### 4.3 Get Task Status

**`GET /api/v1/tasks/{task_id}`**

Response **200**:

```json
{
  "task_id": "3b3a1cec-d262-4b69-a2fc-832e6f6a392c",
  "type": "jobs",
  "status": "completed",
  "workflow_state": "complete",
  "created_at": "2026-08-22T09:00:00Z",
  "started_at": "2026-08-22T09:00:01Z",
  "completed_at": "2026-08-22T09:01:20Z",
  "error": null
}
```

**`status` values:**

| Value | Meaning |
|-------|---------|
| `pending` | Created, not started |
| `running` | Agent is searching/processing |
| `paused` | **Human takeover needed** (MFA, CAPTCHA, blocker) |
| `waiting_approval` | Awaiting approval for an external action |
| `completed` | Done; fetch results |
| `failed` | Error; see `error` field |

### 4.4 Get Results

**`GET /api/v1/tasks/{task_id}/results`**

Response **200**:

```json
{
  "task_id": "3b3a1cec-d262-4b69-a2fc-832e6f6a392c",
  "status": "completed",
  "summary": "10 ranked results",
  "results": [
    {
      "id": "809e7168-7c59-4d67-a1e4-92d69f0eac68",
      "title": "Chean Wei Yap",
      "subtitle": "Hands-On Software Architect | Principal / Staff Engineer...",
      "location": "Singapore, Singapore",
      "source": "linkedin_people",
      "source_url": "https://www.linkedin.com/in/yap-chean-wei/",
      "match_score": 90.0,
      "match_reason": "20+ yrs, Singapore-based, hands-on architect with strong Spring Boot/microservices/AWS evidence, high credibility (88)...",
      "evidence": [],
      "gaps": [],
      "status": "new",
      "summary": "About\n\nI architect systems that don't just work today...",
      "skills": ["Microservices", "AWS", "Java"],
      "experience": "Experience\n\nTechnical Architect\n...",
      "education": "Education\n\nNational University of Singapore...",
      "certifications": "Licenses & certifications\n...",
      "credibility": {
        "score": 88.0,
        "title_inflation": 0.0,
        "tenure_depth": 1.0,
        "evidence_ratio": 0.8,
        "flags": []
      }
    }
  ]
}
```

---

## 5. Result Schema (`results[]`)

A result is a **job** or a **candidate** depending on the task type.

### Common fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Result entity ID |
| `title` | string | Job title (jobs) or candidate name (candidates) |
| `subtitle` | string\|null | Company (jobs) or headline (candidates) |
| `location` | string\|null | Location |
| `source` | string | `linkedin`, `linkedin_people`, etc. |
| `source_url` | string\|null | Source link |
| `match_score` | float (0–100) | **Ranked relevance score** |
| `match_reason` | string\|null | Human-readable explanation |
| `evidence` | array | Traceable evidence entries |
| `gaps` | array | Missing requirements |
| `recommended_action` | string\|null | Suggested next step |
| `status` | string | `new` / `approved` / `rejected` |

### Evidence entry

```json
{
  "field": "mandatory_skills",
  "value": "Matched required skills: java, kafka",
  "source_url": "https://...",
  "source_text": null
}
```

### Candidate-only fields

| Field | Type | Description |
|-------|------|-------------|
| `summary` | string\|null | "About" profile text |
| `skills` | array | Extracted top skills |
| `experience` | string\|null | Full experience section |
| `education` | string\|null | Education section |
| `certifications` | string\|null | Licenses/certs section |
| `credibility` | object\|null | Signal-validated credibility |

### Credibility object

```json
{
  "score": 88.0,
  "title_inflation": 0.0,
  "tenure_depth": 1.0,
  "evidence_ratio": 0.8,
  "flags": ["Headline claims leadership but experience shows no real leadership roles"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `score` | float 0–100 | Overall credibility (higher = more evidence-backed) |
| `title_inflation` | float 0–1 | Degree of title inflation detected (AVP/VP at banks, grand titles with short tenure) |
| `tenure_depth` | float 0–1 | Tenure depth (longer, fewer stints = higher) |
| `evidence_ratio` | float 0–1 | Fraction of claimed skills evidenced in experience text |
| `flags` | array | Human-readable warnings (resume padding, job-hopping, etc.) |

---

## 6. Errors

All errors use a **unified envelope**:

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

### Error codes

| HTTP | `code` | Meaning |
|------|--------|---------|
| 400 | `bad_request` | Malformed request |
| 401 | `unauthorized` | Missing/invalid API key |
| 403 | `forbidden` | Not permitted |
| 404 | `not_found` | Task/entity not found |
| 422 | `validation_error` | Body failed schema validation (`details` has field errors) |
| 429 | `rate_limited` | Rate limit exceeded (has `Retry-After` header) |
| 500 | `internal_error` | Server error |

### 422 example

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "status": 422,
    "details": [
      {"loc": "body.query", "msg": "Field required"}
    ]
  }
}
```

---

## 7. Browser Session Endpoints

These let callers manage the underlying browser and request human takeover.

### Create session

**`POST /api/v1/browser/sessions`** → **201**

```json
{ "session_id": "70415271-7265-4510-b16f-bd8db5b76b8a", "status": "idle" }
```

### Human takeover

**`POST /api/v1/browser/{session_id}/takeover`** with body:

```json
{ "action": "start" }        // hand control to a human
{ "action": "return" }       // return control to the agent
{ "action": "status" }       // just check status
```

### Observe

**`GET /api/v1/browser/{session_id}/observe`** → current URL + title.

---

## 8. Approvals

External actions (messages, connections, applications) require approval.

**`POST /api/v1/approvals/{approval_id}`**

```json
{ "decision": "approve", "note": "Looks good" }
```

`decision` is `approve` or `reject`.

---

## 9. Worked Examples (curl)

### Job search, full flow

```bash
KEY="YOUR_API_KEY"
BASE="http://localhost:8000"

# 1. Start
TASK=$(curl -s -X POST "$BASE/api/v1/search/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"query":"AI platform leadership roles","location":"Singapore"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# 2. Poll (every 5s, max 60s)
for i in $(seq 1 12); do
  STATUS=$(curl -s "$BASE/api/v1/tasks/$TASK" -H "X-API-Key: $KEY" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "poll $i: $STATUS"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && echo "FAILED" && break
  sleep 5
done

# 3. Results
curl -s "$BASE/api/v1/tasks/$TASK/results" -H "X-API-Key: $KEY"
```

### Candidate search (single call)

```bash
curl -s -X POST "$BASE/api/v1/search/candidates" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"query":"Java, Kafka, microservices, banking","location":"Singapore"}'
```

### Python client example

```python
import time
import httpx

BASE = "http://localhost:8000"
KEY = "YOUR_API_KEY"
HEADERS = {"X-API-Key": KEY}

with httpx.Client(base_url=BASE, headers=HEADERS, timeout=60) as client:
    # Start candidate search
    r = client.post("/api/v1/search/candidates", json={
        "query": "Java, Kafka, microservices, banking",
        "location": "Singapore",
    })
    task_id = r.json()["task_id"]

    # Poll until done
    while True:
        status = client.get(f"/api/v1/tasks/{task_id}").json()["status"]
        if status in ("completed", "failed", "paused"):
            break
        time.sleep(5)

    # Get ranked results
    results = client.get(f"/api/v1/tasks/{task_id}/results").json()
    for item in results["results"]:
        print(f"{item['match_score']:>5}  {item['title']}  {item['subtitle']}")
```

---

## 10. Integration Notes for AI Agents

- **Treat the API as async-first.** Never assume a search is instant; poll
  `GET /tasks/{id}`.
- **`status: "paused"` means a human must act.** If you hit this, stop and
  surface it — do not retry blindly. The browser needs human takeover
  (MFA/CAPTCHA/blocker).
- **`match_score` is 0–100**, higher is better. Use it for ranking, but read
  `match_reason` and `credibility.flags` for context — scores are
  evidence-informed, not gospel.
- **Candidate `credibility` matters.** `title_inflation`, `evidence_ratio`,
  and `flags` indicate inflated or unverified claims. Prefer candidates with
  high credibility + high match.
- **Every recommendation is traceable** via `evidence[]` and `source_url`.
- **Rate limits are per key.** Respect `429` + `Retry-After`.
- **Send `X-Request-ID`** on requests to correlate your logs with server
  errors (it echoes back in error envelopes).
- **Poll politely**: 5s interval is fine; don't hammer.

---

## 11. Configuration Reference (`backend/.env`)

| Var | Default | Description |
|-----|---------|-------------|
| `API_KEYS` | empty | Comma-separated keys, optional `:N` per-key req/min |
| `API_RATE_LIMIT_PER_MIN` | 30 | Default rate limit |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed web origins |
| `LLM_ENABLED` | false | Enable GLM LLM reranking |
| `LLM_BASE_URL` | z.ai anthropic | LLM endpoint |
| `PACING_*` | — | Human-like browser pacing (anti-detection) |
