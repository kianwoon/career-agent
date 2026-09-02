# Find Candidates API Spec

> Integration spec for external systems calling the candidate search endpoint.
> Full service API reference: [`docs/API.md`](./API.md) · Machine-readable spec: `GET /openapi.json`

---

## 1. Overview

| Item | Value |
|------|-------|
| **Endpoint** | `POST /api/v1/search/candidates` |
| **Base URL (dev)** | `http://localhost:8000` |
| **Auth** | `X-API-Key` header (required) |
| **Model** | **Async**: start task → poll status → fetch results (30–120s) |
| **Platforms** | `linkedin` + any enabled source with an active `find_candidates` flow (currently also `jobstreet - candidate`) |
| **Rate limit** | Per API key, default 30 req/min (`429` + `Retry-After` on breach) |

### Integration flow

```
POST /search/candidates ──► 201 { task_id }
        │
        ▼  poll every 5s
GET /tasks/{task_id} ──► until status = completed | failed | paused
        │
        ▼
GET /tasks/{task_id}/results ──► ranked candidate list
```

---

## 2. Start a Candidate Search

### `POST /api/v1/search/candidates`

Headers:

```
X-API-Key: YOUR_API_KEY
Content-Type: application/json
```

#### Request body

Either **simple mode** (single `query`) or **plan mode** (`queries` + plan fields).

```json
{
  "query": "Java, Kafka, microservices, banking",
  "queries": ["Java AND Kafka", "payments engineer AND microservices"],
  "exclude": ["intern", "unpaid"],
  "platforms": ["linkedin", "jobstreet - candidate"],
  "platform": "LinkedIn",
  "location": "Singapore",
  "salary": "SGD 120k-180k",
  "employment_type": "Full-time",
  "sources": ["<source_id>"]
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `query` | string\|null | one of `query`/`queries` | — | Simple candidate criteria; treated as a one-query plan when `queries` absent |
| `queries` | string[]\|null | one of `query`/`queries` | soft cap **5** | Boolean search queries, run in sequence and merged. **More than 5 is accepted** and truncated to the first 5 |
| `exclude` | string[]\|null | no | soft cap **10** | Terms excluded via `NOT (...)` and post-filter. **More than 10 is accepted** and truncated to the first 10 |
| `platforms` | string[]\|null | no | soft cap **5** | Search platforms run **in sequence and merged** (e.g. `["linkedin", "jobstreet - candidate"]`); results are combined, ranked, and the **top 10** returned. More than 5 is accepted and truncated. Unknown platform **names** are rejected with `422` |
| `platform` | string\|null | no | — | Legacy single platform (same as one-element `platforms[]`); prefer `platforms` |
| `location` | string\|null | no | — | Location filter, e.g. `"Singapore"` |
| `salary` | string\|null | no | — | Salary context (ranking criteria only, not searchable) |
| `employment_type` | string\|null | no | — | Employment type (ranking criteria only) |
| `sources` | string[]\|null | no | — | Source IDs to include; null/empty = all enabled sources |

Valid platform ids: `linkedin` plus any enabled source with an active
`find_candidates` flow (discover them via `GET /api/v1/search/platforms`).
Platform names are case-insensitive.

#### Validation errors

| Condition | Response |
|-----------|----------|
| No `query` and no `queries` | `422` — "Provide `queries` (list) or `query` (string)" |
| Unsupported platform **name** (e.g. typo) | `422` — lists supported platforms |
| Missing/invalid API key | `401` |

> **Oversized inputs are never rejected.** `queries`, `exclude`, and
> `platforms` accept any count; the server silently truncates to the caps
> (5 / 10 / 5) and runs the first N. External systems can always send
> their full list without a `422`.

#### Response `201`

```json
{
  "task_id": "3b3a1cec-d262-4b69-a2fc-832e6f6a392c",
  "type": "candidates",
  "status": "pending",
  "workflow_state": null,
  "created_at": "2026-08-22T09:00:00Z",
  "started_at": null,
  "completed_at": null,
  "error": null
}
```

---

## 3. Poll Task Status

### `GET /api/v1/tasks/{task_id}`

Response `200`:

```json
{
  "task_id": "3b3a1cec-d262-4b69-a2fc-832e6f6a392c",
  "type": "candidates",
  "status": "completed",
  "workflow_state": "complete",
  "created_at": "2026-08-22T09:00:00Z",
  "started_at": "2026-08-22T09:00:01Z",
  "completed_at": "2026-08-22T09:01:20Z",
  "error": null
}
```

| `status` | Meaning | Caller action |
|----------|---------|---------------|
| `pending` | Created, not started | Keep polling |
| `running` | Agent is searching/processing | Keep polling |
| `paused` | **Human takeover needed** (MFA/CAPTCHA/blocker) | **Stop.** Surface to a human; do not retry |
| `waiting_approval` | Awaiting approval for external action | Resolve via approvals endpoint or wait |
| `completed` | Done | Fetch results |
| `failed` | Error | Read `error` field |

Poll interval: **5s recommended** — do not hammer.

---

## 4. Fetch Results

### `GET /api/v1/tasks/{task_id}/results`

Response `200`:

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
      "source_platform": "LinkedIn",
      "source_url": "https://www.linkedin.com/in/yap-chean-wei/",
      "match_score": 90.0,
      "match_reason": "20+ yrs, Singapore-based, hands-on architect...",
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

### Result fields

**Common:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Result entity ID |
| `title` | string | Candidate name |
| `subtitle` | string\|null | Headline |
| `location` | string\|null | Location |
| `source` | string | Raw platform id, e.g. `linkedin_people`, `jobstreet - candidate` |
| `source_platform` | string\|null | **Display-friendly platform label** — `LinkedIn`, `JobStreet`, `MyCareersFuture`, `FastJobs` — for UI badges |
| `source_url` | string\|null | **Openable link to the candidate's page on the source platform.** LinkedIn results carry the real profile URL; flow-based platforms that expose no per-candidate deep links (e.g. SEEK/JobStreet) carry the platform's search page — open it and search the candidate name |
| `match_score` | float 0–100 | Ranked relevance score (higher = better) |
| `match_reason` | string\|null | Human-readable explanation |
| `evidence` | array | Traceable evidence entries `{field, value, source_url, source_text}` |
| `gaps` | array | Missing requirements |
| `recommended_action` | string\|null | Suggested next step |
| `status` | string | `new` / `approved` / `rejected` |

**Candidate-only:**

| Field | Type | Description |
|-------|------|-------------|
| `summary` | string\|null | "About" profile text |
| `skills` | array | Extracted top skills |
| `experience` | string\|null | Full experience section |
| `education` | string\|null | Education section |
| `certifications` | string\|null | Licenses/certs section |
| `credibility` | object\|null | Signal-validated credibility |

### Credibility object

| Field | Type | Description |
|-------|------|-------------|
| `score` | float 0–100 | Overall credibility (higher = more evidence-backed) |
| `title_inflation` | float 0–1 | Degree of title inflation detected |
| `tenure_depth` | float 0–1 | Tenure depth (longer, fewer stints = higher) |
| `evidence_ratio` | float 0–1 | Fraction of claimed skills evidenced in experience |
| `flags` | array | Warnings (resume padding, job-hopping, inflated titles...) |

---

## 5. Error Envelope (all endpoints)

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

| HTTP | `code` | Meaning |
|------|--------|---------|
| 400 | `bad_request` | Malformed request |
| 401 | `unauthorized` | Missing/invalid API key |
| 403 | `forbidden` | Not permitted |
| 404 | `not_found` | Task/entity not found |
| 422 | `validation_error` | Body failed schema validation (`details` has field errors) |
| 429 | `rate_limited` | Rate limit exceeded (has `Retry-After` header) |
| 500 | `internal_error` | Server error |

---

## 6. End-to-End Example (curl)

```bash
KEY="YOUR_API_KEY"
BASE="http://localhost:8000"

# 1. Start
TASK=$(curl -s -X POST "$BASE/api/v1/search/candidates" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"queries":["Java AND Kafka AND payments"],"exclude":["intern"],"platforms":["linkedin","jobstreet - candidate"],"location":"Singapore"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# 2. Poll (every 5s, max ~2min)
for i in $(seq 1 24); do
  STATUS=$(curl -s "$BASE/api/v1/tasks/$TASK" -H "X-API-Key: $KEY" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "poll $i: $STATUS"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && echo "FAILED" && break
  [ "$STATUS" = "paused" ] && echo "HUMAN NEEDED" && break
  sleep 5
done

# 3. Results — merged from ALL requested platforms, ranked, top 10
curl -s "$BASE/api/v1/tasks/$TASK/results" -H "X-API-Key: $KEY"
```

### Python client example

```python
import time
import httpx

BASE = "http://localhost:8000"
KEY = "YOUR_API_KEY"
HEADERS = {"X-API-Key": KEY}

with httpx.Client(base_url=BASE, headers=HEADERS, timeout=60) as client:
    r = client.post("/api/v1/search/candidates", json={
        "query": "Java, Kafka, microservices, banking",
        "location": "Singapore",
    })
    task_id = r.json()["task_id"]

    while True:
        status = client.get(f"/api/v1/tasks/{task_id}").json()["status"]
        if status in ("completed", "failed", "paused"):
            break
        time.sleep(5)

    results = client.get(f"/api/v1/tasks/{task_id}/results").json()
    for item in results["results"]:
        platform = item.get("source_platform") or item["source"]
        print(f"{item['match_score']:>5}  [{platform}]  {item['title']}  —  {item.get('source_url')}")
```

---

## 7. Integration Notes

- **Async-first.** Never assume instant results; poll `GET /tasks/{id}`.
- **`paused` = human required.** Stop and surface; do not retry blindly.
- **Respect 429 + `Retry-After`.** Rate limits are per API key.
- **Send `X-Request-ID`** to correlate your logs with server errors (echoed in the error envelope).
- **Rank by `match_score`**, but read `match_reason`, `credibility.flags`, and `gaps` — prefer high match + high credibility.
- **Show `source_platform`** in UIs; use `source` (raw id) for programmatic filtering.
- **`source_url` is always openable**, but semantics differ per platform: LinkedIn = direct profile URL; SEEK/JobStreet = platform search page (search the candidate name there — no public per-candidate deep links exist).
- **Every result is traceable** via `evidence[]` and `source_url`.
- **Auth config (server-side):** `API_KEYS="key1:60,key2:30"` in `backend/.env` (`:N` = req/min cap).

---

## 8. Related Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Liveness (public, no key) |
| GET | `/api/v1/tasks/{task_id}` | Task status |
| GET | `/api/v1/tasks/{task_id}/results` | Ranked results |
| POST | `/api/v1/approvals/{approval_id}` | Approve/reject pending external action |
| GET | `/openapi.json` | Full machine-readable spec |
