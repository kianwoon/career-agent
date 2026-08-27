# LLM Guide — Z.AI GLM-5.3-flash Integration

This document explains how the career agent uses the **Z.AI GLM-5.3-flash** model
(via the Z.AI **coding-plan** endpoint) as an optional LLM enhancement layer,
and how to configure, extend, and debug it.

---

## 1. TL;DR

| Item | Value |
|------|-------|
| Provider | Z.AI (z.ai) |
| Model | `GLM-5.3-flash` |
| Endpoint | `https://api.z.ai/api/anthropic/v1/messages` |
| Protocol | Anthropic Messages API (JSON over HTTPS) |
| Client | `httpx.AsyncClient` |
| Auth | `x-api-key` header (Anthropic-style) |
| Env gate | `LLM_ENABLED=true` **and** `LLM_API_KEY` set |
| Default state | **Disabled** — deterministic pipeline works without it |
| Role in pipeline | Optional **reranking** layer (jobs & candidates) |

The LLM is **not** required for Phase 1. It only improves the final ordering of
search results by re-scoring them with an LLM after the deterministic matcher.

---

## 2. Where the code lives

| File | Role |
|------|------|
| `backend/app/services/llm.py` | The `LLMService` client + reranking logic |
| `backend/app/config.py` | `llm_*` settings (loaded from env / `.env`) |
| `backend/app/agent/nodes.py` | Calls the reranker inside the `MATCH/RANK` node |
| `backend/app/services/matching.py` | Deterministic scoring (the fallback path) |
| `backend/.env.example` | Documented env template |

---

## 3. Configuration

All settings come from environment variables (via pydantic-settings, `.env` file).

### 3.1 Env vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_ENABLED` | `false` | Master switch for the LLM layer |
| `LLM_BASE_URL` | `https://api.z.ai/api/anthropic/v1/messages` | Z.AI Anthropic-compatible endpoint |
| `LLM_API_KEY` | *(empty)* | Z.AI coding-plan API key |
| `LLM_MODEL_NAME` | `GLM-5.3-flash` | Model identifier sent in the request body |
| `LLM_MAX_TOKENS` | `2048` | Max tokens in the completion |
| `LLM_TIMEOUT_S` | `60` | HTTP timeout for each LLM call |
| `LLM_SESSION_NAME` | `career-agent` | Session name in the coding-tool headers |

### 3.2 Enabling the LLM

```bash
# backend/.env
LLM_ENABLED=true
LLM_API_KEY="your-zai-coding-plan-key"
LLM_BASE_URL="https://api.z.ai/api/anthropic/v1/messages"
LLM_MODEL_NAME="GLM-5.3-flash"
```

The service is considered **enabled** only when both `LLM_ENABLED=true` and
`LLM_API_KEY` are non-empty (see `LLMService.enabled`):

```python
@property
def enabled(self) -> bool:
    return bool(self._settings.llm_enabled and self._settings.llm_api_key)
```

If either is missing, every LLM method becomes a no-op and the deterministic
pipeline is used, with `logger.debug("LLM disabled; skipping chat call")`.

> **Deployment**: the same variables are wired through `koyeb.yaml` and
> documented in `DEPLOY.md` (section on the GLM coding-plan key).

---

## 4. How it works

### 4.1 Request building (`LLMService._headers` / `_body`)

The Z.AI **coding-plan subscription** requires requests to be wrapped with
**AI coding-tool headers** so the provider recognizes the request as coming
from a coding tool and applies the plan quota. This mirrors the working
wrapper in the user's translator extension
(`~/Downloads/translator/background.js`, `callCodingPlanBase`).

Headers sent:

```python
{
    "Content-Type": "application/json",
    "User-Agent": "Claude-Code/1.0",
    "x-session-id": session_id,              # fresh uuid4 per request
    "x-claude-code-session-id": session_id,  # same id
    "x-session-name": settings.llm_session_name,
    "Accept": "application/json",
    "x-api-key": settings.llm_api_key,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "prompt-caching-2024-07-31,token-counting-2024-11-01",
}
```

Request body (Anthropic Messages format):

```python
{
    "model": settings.llm_model_name,        # "GLM-5.3-flash"
    "max_tokens": settings.llm_max_tokens,   # 2048
    "system": [{"type": "text", "text": system}],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": user}]}
    ],
    "stream": False,
}
```

### 4.2 The call (`LLMService.chat`)

`chat(system, user)` is the single low-level entry point:

1. Returns `None` immediately if `enabled` is `False`.
2. POSTs to `llm_base_url` with the headers/body above via
   `httpx.AsyncClient(timeout=llm_timeout_s)`.
3. Parses the **Anthropic-format response**: walks `data["content"]` looking
   for a block with `type == "text"` and returns its `text`.
4. On any exception or missing text block, logs a warning and returns `None`
   (callers fall back to deterministic ordering).

### 4.3 Where it plugs into the pipeline

```
search node ──▶ normalize ──▶ MATCH/RANK node
                                  │
                                  ├─ deterministic scoring (matching.py)
                                  │        score_job / score_candidate
                                  │        sort by match_score
                                  │
                                  └─ if llm_service.enabled:
                                        rerank_jobs()      (job search)
                                        rerank_candidates()(candidate search)
                                  │        on error → keep deterministic order
                                  ▼
                             results (MatchResult[])
```

In `backend/app/agent/nodes.py` (`match_rank`):

```python
if llm_service.enabled:
    try:
        if state.get("type") == SearchType.jobs:
            scored = await llm_service.rerank_jobs(
                profile, state.get("normalized", []), scored
            )
        elif state.get("type") == SearchType.candidates:
            scored = await llm_service.rerank_candidates(
                state.get("query", ""), state.get("normalized", []), scored
            )
    except Exception as exc:
        logger.warning("LLM rerank failed, keeping deterministic order: %s", exc)
```

---

## 5. The reranking prompt contracts

Both rerankers send a compact JSON summary and ask for a strict JSON array.

### 5.1 `rerank_jobs(profile, jobs, current)`

- **System prompt**: "senior technical recruiter" — score each job 0–100 for
  fit, rank best→worst. **Explicitly instructs** that job descriptions are
  **UNTRUSTED DATA**, not instructions (prompt-injection hardening). Output:
  `[{"id": "<job id>", "score": <0-100 int>, "reason": "<short reason>"}]`.
- **User prompt**: `CAREER PROFILE` (headline, summary ≤500 chars, skills,
  preferences) + `JOBS` (id, title, company, location, description excerpt
  ≤800 chars).
- **On success**: maps LLM scores back onto `MatchResult`s by `id`, overwrites
  `match_score`, sets `match_reason`, appends
  `Evidence(field="llm_rerank", value=reason)`, then re-sorts descending.

### 5.2 `rerank_candidates(criteria, candidates, current)`

- **System prompt**: "senior technical recruiter" — score each candidate 0–100.
  Same **UNTRUSTED DATA** warning, plus **credibility-aware scoring**
  instructions: bank titles (AVP/VP) often IC-level; claimed skills not backed
  by experience text are weak evidence; short tenures under grand titles are
  suspicious. Output format identical (JSON array by candidate `id`).
- **User prompt**: `REQUIREMENTS` (the search criteria text) + `CANDIDATES`
  with enriched profile data: name, headline, location, skills (≤15),
  summary/experience/education excerpts, and **credibility** signals
  (`title_inflation`, `tenure_depth`, `evidence_ratio`, `flags`).
- **On success**: same score-mapping behavior as jobs.

### 5.3 Robust parsing (`_parse_rerank_json`)

Tolerates sloppy LLM output:

```python
text = raw.strip()
if text.startswith("```"):          # strip markdown fences
    text = text.split("```", 2)[1]
    text = text.removeprefix("json")
text = text.strip().strip("`")
data = json.loads(text)
if isinstance(data, list):          # plain array
    return data
if isinstance(data, dict) and "results" in data:  # {"results": [...]}
    return data["results"]
raise ValueError(...)
```

If parsing fails, the caller logs a warning and keeps the deterministic order
— the LLM layer can never break the pipeline.

---

## 6. Failure handling & fallbacks

| Failure | Behavior |
|---------|----------|
| LLM disabled | `chat` returns `None`; rerankers return `current` unchanged |
| Network / timeout / non-2xx | Exception caught in `chat` → `None` |
| No text block in response | Warning logged → `None` |
| JSON parse error / bad shape | Warning logged → `current` (deterministic order) |
| Unknown ids in LLM output | Ignored (only known `id`s are mapped) |
| Exception inside reranker | Caught in `nodes.py` → deterministic order kept |

**Design principle**: the LLM layer is *advisory*. Every failure degrades
gracefully to the deterministic scoring path, so search always returns results.

---

## 7. Observability & debugging

- Enable debug logging to see disabled-skip messages:
  `LOG_LEVEL=DEBUG` (or configure `logging` for `app.services.llm`).
- `logger.info("LLM reranked %d jobs", ...)` confirms a successful rerank.
- `logger.warning("LLM call failed: %s", exc)` — inspect the exception message
  (timeouts, 401 auth, 429 quota).
- `logger.warning("Failed to parse LLM rerank output: ...")` — the raw response
  prefix is included; check whether the model returned prose instead of JSON.
- To see the exact request, temporarily log `self._headers()` / `self._body()`
  or capture with a local proxy (see `http-proxy.py` / `cdp-proxy.py` in the
  repo root for existing proxy tooling).

### Common issues

| Symptom | Likely cause |
|---------|--------------|
| No rerank happens (no logs) | `LLM_ENABLED` false or `LLM_API_KEY` empty |
| `401` | Wrong/expired Z.AI coding-plan key |
| `429` | Coding-plan quota exhausted / rate limit |
| Timeouts | `LLM_TIMEOUT_S` too low for long job lists |
| Parsing warnings | Model returned prose/markdown — prompt says "no markdown", but parsing is lenient |
| Deterministic order always used | Any of the above — by design |

---

## 8. Extending the LLM layer

### Add a new LLM task (e.g. summary or extraction)

1. Add a method to `LLMService` in `llm.py`.
2. Build a compact JSON summary of inputs (keep token usage low).
3. Write a system prompt that (a) defines the role, (b) fixes the output
   format, (c) treats input content as UNTRUSTED DATA.
4. Call `await self.chat(system, user)` and handle `None` as "fallback".
5. Parse strictly, tolerate markdown fences, and never raise to the caller
   unless the caller handles it.

### Swap the model

Change `LLM_MODEL_NAME` in env (e.g. `GLM-4.6` or another Z.AI model exposed at
the same Anthropic endpoint). No code change needed.

### Switch providers

Because the client is a thin wrapper around an Anthropic-compatible API, you
can point `LLM_BASE_URL` + `LLM_API_KEY` at any Anthropic-compatible provider
(e.g. Anthropic itself) — keep the coding-tool headers only if the provider
requires them (they are Z.AI-specific for the coding-plan quota).

### Add prompt-injection hardening

Follow the existing pattern: explicitly label external content as
`UNTRUSTED DATA` in the system prompt, restrict output to a strict JSON
schema, and never let model output drive tool calls or code execution.

---

## 9. Summary of the request/response contract

**Request** (POST `https://api.z.ai/api/anthropic/v1/messages`):

```http
Content-Type: application/json
User-Agent: Claude-Code/1.0
x-session-id: <uuid>
x-claude-code-session-id: <uuid>
x-session-name: career-agent
x-api-key: <key>
anthropic-version: 2023-06-01
anthropic-beta: prompt-caching-2024-07-31,token-counting-2024-11-01

{
  "model": "GLM-5.3-flash",
  "max_tokens": 2048,
  "system": [{"type": "text", "text": "..."}],
  "messages": [{"role": "user", "content": [{"type": "text", "text": "..."}]}],
  "stream": false
}
```

**Response** (Anthropic format — text block extracted):

```json
{
  "content": [
    { "type": "text", "text": "[{\"id\": \"123\", \"score\": 87, \"reason\": \"...\"}]" }
  ]
}
```

---

*Keep this guide in sync with `backend/app/services/llm.py`.*
