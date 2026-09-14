# Graph Report - career bot  (2026-09-11)

## Corpus Check
- 131 files · ~94,504 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1510 nodes · 2773 edges · 130 communities (112 shown, 16 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 176 edges (avg confidence: 0.93)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3b23a3e6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- api.ts
- AsyncSession
- BrowserSession
- matching.py
- AgentState
- test_session.py
- AgentRegistry
- PacingService
- test_mycareersfuture.py
- routes.py
- fastjobs.py
- linkedin_people.py
- background.js
- assess_credibility
- test_source_flows.py
- manifest.json
- schemas.py
- compilerOptions
- WizardSession
- package.json
- test_sourcing_plan.py
- cdp-proxy.py
- tunnel.sh
- nodes.py
- Base
- source_flows.py
- main.py
- Career Agent System
- agent.py
- get_settings
- test_candidates.py
- _apply_excludes
- dedupe_candidates
- _gate_relaxed_rows
- test_security.py
- config.py
- test_sources_api.py
- _first_or_group
- proxy_config
- filter_excluded_results
- sources.py
- linkedin.py
- handle_client
- SlidingWindowLimiter
- popup.js
- route.ts
- entrypoint.sh
- package.sh script
- SourceFlow
- middleware.ts
- next.config.mjs
- __init__.py
- .capture_state
- test_sanitize_storage_state_normalizes_chrome_samesite
- Candidate Search Workflow
- Job Search Workflow
- Qdrant Vector Search
- Redis Task State
- LLMService
- career-agent-backend
- _agent_discover
- test_llm_rerank.py
- test_compat_routes.py
- EmbeddingProvider
- BrowserError
- deduplicate
- Career-agent flow recording: SEEK/jobstreet selectors
- SourceFlowView
- browser.py
- _MultiQueryFakeRegistry
- APIKeyStore
- BrowserService
- Runbook: Package a production OpenCode desktop app
- Career-bot revised spec (2026-09-02): integration diff checklist
- _restore_stripped_api_prefix
- _connect
- Disable V1 snapshot event persistence for local builds
- Pluggable job-source architecture (guided wizard)
- Expired source session UX (pause + re-login banner)
- Source session expiry handling
- Wizard runs fully server-side (no local tunnel)
- Default idle stall guard tuned to 180s
- Provider idle stall guard is default-on
- Fork release process and tag-creation retry
- Fork release process (fork.6 pattern)
- Fork releases use 'OpenCode Desktop — <feature>' titles
- Career bot prod browser access — HARD USER CONSTRAINTS (2026-09-01): NEVER propose or implement a tunnel (ngrok/cdp-proxy/ngrok-style) again; user forbade it permanently and forcefully. Also rejected: background launchd daemons, dedicated automation browser/profile, headless-on-Koyeb pitches (unvalidated). Only acceptable direction: drive the user's own already-logged-in browser from INSIDE the browser (existing extension + /v1/agent/execute|poll|result protocol); no debug flags, no external reachability requirements. Extraction logic (boolean queries, NOT excludes, card parsing) is proven; only the transport/trigger needs porting to the extension.
- Desktop build: channel + step order
- COUNT(*) quotas cannot bound upsert endpoints
- DeepInfra LLM calls need longer timeout than Cerebras
- Koyeb env updates collide with in-flight CI deploys
- LLM-paying jobs need attempt caps at claim time
- Per-selector :not() chains explode generated CSS size
- Modal + vLLM serving gotchas
- Modal web_server hangs on vLLM streaming — use @app.server instead
- Qwen3.8-27B on Modal: two root causes (web_server streaming + MTP-3 crash)
- vLLM tool-call parser for Qwen3.5-family models
- Cache-efficiency key reads 0% with self-hosted models
- opencode config: model vision declared with 'capabilities' key is silently ignored
- Confounded A/B: different output lengths + wrong log file led to wrong 'steps 3 wins' conclusion
- DFlash 2 open-PR audit: watch #32052 (GDN state commit + extra_buffer radix)
- GPU instance lifecycle: caches die with instances; always stage launch scripts + know your rebuild path
- OpenCode custom-provider vision: use modalities.input not capabilities.input
- Runbook lagged live config on chunked-prefill
- Shell: outer truncate-redirect clobbers inner appends to the same log file
- Subagents must not pin a model in frontmatter if they should inherit the primary agent's model
- User protocol: no unsolicited advice, execute instructions only
- vast.ai launch-mode quirks (4 failures to 1 success)
- vast/RunPod template: never pip-install SGLang from a moving PR ref
- vastai update template wipes unspecified fields + needs hash id
- Duplicate user messages: prompt persists before joining run
- Self-hosted vLLM 0% cache hit rate is server-side, not opencode
- syv-ai 381tok/s stack is Ampere+patched-vLLM only, not portable to sm120
- Upstream opencode sync: cherry-pick workflow + fork-local quirks
- tmp profile copy gets wiped by macOS
- MV3 extension — content script single-shot GET_STATE dies until reload
- Bitdeer API rejects suffixed model IDs
- CFPreferences dict values as NSString break Swift as?-cast — the resurfacing 'settings gone' master cause
- opencode.json custom model needs attachment:true for images
- Vast 6000 template switches SGLang+DFlash2 to vLLM MTP-2 unsloth-NVFP4
- vast.ai template onstart 16384-char cap breaks base64 scripts
- Brave extensions deadlock Playwright connectOverCDP
- Chrome policy force-install is not a novice install path
- Wizard headed-browser 502 on Koyeb
- npm plugin entry resolution + Node ESM plugin imports
- opencode model config: 3 conflicting layers silently fell back to glm-4.7
- Brave CDP wedges after repeated Playwright connect_over_cdp cycles (each browser-level attach re-enumerates all targets; endpoint degrades ~10-15 min after launch; ws handshake times out even at 180s). FIX: hold ONE connection per plan — career bot linkedin_people.py _PlanSession (lazy connect thunk = _connect_with_best_session; reuse the SAME page it returns; the pair is (playwright_manager, page) and the manager has NO .contexts). Set connect timeout 15s fail-fast. Also: pkill -f 'uvicorn app.main' before backend restart or stale code serves; results endpoint is GET /api/v1/tasks/{id}/results (no /search prefix). E2E verified: task 8f6d1074 completed 210s, 9 candidates, plan_detail 'query3: 10 -> 9 unique'.
- Koyeb prod career-agent API: Koyeb route rule strips '/api' prefix, AND the FastAPI router itself carries prefix '/api/v1' — so prod paths are DOUBLED: POST https://career-agent-kianwoon-88223cd5.koyeb.app/api/v1/api/v1/search/candidates (same for /tasks/{id}/results). Prod API key lives in Koyeb env API_KEYS (career_...:60), NOT dev-e2e-key. Tunnel chain (Brave:9222 -> cdp-proxy:9999 -> ngrok -> Koyeb) is restored via ./tunnel.sh start (auto-updates Koyeb BRAVE_CDP_URL env) + ./tunnel.sh verify; Brave must be launched with --remote-debugging-port=9222 --remote-allow-origins='*' --disable-extensions (extensions deadlock Playwright CDP attach per docs/CDP-TUNNEL-RUNBOOK.md). Verified: prod task 942db35d completed 165s, 9 candidates via tunnel.

## God Nodes (most connected - your core abstractions)
1. `Home()` - 42 edges
2. `resolveApiBase()` - 34 edges
3. `addEvent()` - 25 edges
4. `BrowserError` - 24 edges
5. `Base` - 23 edges
6. `WizardSession` - 23 edges
7. `AgentState` - 22 edges
8. `SearchType` - 22 edges
9. `SourceFlow` - 21 edges
10. `postJson()` - 21 edges

## Surprising Connections (you probably didn't know these)
- `CDP Tunnel to Local Brave` --references--> `cmd_start()`  [INFERRED]
  docs/CDP-TUNNEL-RUNBOOK.md → tunnel.sh
- `Openable source_url Fallback` --references--> `_normalize_flow_candidate()`  [INFERRED]
  docs/CANDIDATES-API.md → backend/app/agent/nodes.py
- `Multi-Platform Candidate Sourcing` --references--> `_search_custom_sources()`  [INFERRED]
  docs/CANDIDATES-API.md → backend/app/agent/nodes.py
- `Async-First Search API` --references--> `start_job_search()`  [INFERRED]
  docs/API.md → backend/app/api/routes/routes.py
- `Async-First Search API` --references--> `start_candidate_search()`  [INFERRED]
  docs/API.md → backend/app/api/routes/routes.py

## Import Cycles
- None detected.

## Communities (130 total, 16 thin omitted)

### Community 0 - "api.ts"
Cohesion: 0.05
Nodes (98): addEvent(), ConnectBrowserAgent(), Home(), addEventPrev(), clearWizPolls(), ensureSession(), handleAddSource(), handleAgentCaptureSession() (+90 more)

### Community 1 - "AsyncSession"
Cohesion: 0.12
Nodes (29): agent_login(), agent_record(), agent_record_manual_start(), agent_record_manual_stop(), agent_session_capture(), AgentRecordRequest, delete_flow(), delete_source() (+21 more)

### Community 2 - "BrowserSession"
Cohesion: 0.18
Nodes (6): BrowserSession, Any, Extract structured data per a schema of field name -> selector., No-op in Phase 1 local mode; signals Steel to freeze the session., Thin wrapper over a Playwright page with a restricted command surface. In Phase…, Human Takeover Flow

### Community 3 - "matching.py"
Cohesion: 0.13
Nodes (21): Evidence, A single piece of traceable evidence backing a match score., _build_reason(), _find_gaps(), keyword_overlap(), NoopEmbeddingProvider, Matching engine: hybrid scoring pipeline per the Phase 1 design spec. Pipeline:…, Score one candidate against a job description. Uses enriched profile data when… (+13 more)

### Community 4 - "AgentState"
Cohesion: 0.10
Nodes (35): build_graph(), LangGraph supervisor graph definition., Build the Phase 1 supervisor graph., AgentState, check_human(), extract(), _log(), match_rank() (+27 more)

### Community 5 - "test_session.py"
Cohesion: 0.09
Nodes (36): decrypt_session_state(), _derive_key(), encrypt_session_state(), _get_key(), Encrpytion service for browser session state (cookies + localStorage). Uses…, Get the 32-byte AES key from config or derive a dev key., Encrypt a JSON string (Playwright storage_state) into a base64 blob. Format:…, Decrypt a base64 blob back into the original JSON string. Returns the raw… (+28 more)

### Community 6 - "AgentRegistry"
Cohesion: 0.06
Nodes (36): AgentRegistry, Command, Any, Browser-extension agent relay — HTTP polling edition. The MV3 service worker +…, Enqueue a command and await the extension's result. lock_wait_s bounds the wait…, Extension asks for the next command (oldest first)., Command queue + result store for the polling extension agent., Record the polling worker instance; fail orphaned commands. A worker reload… (+28 more)

### Community 7 - "PacingService"
Cohesion: 0.07
Nodes (21): Any, QueryCache, Simple in-memory result cache to avoid re-hitting LinkedIn for the same query.…, LRU cache keyed by (query, location) with time-based expiry., PacingService, Any, Enforce max pages per minute. Sleep if we're over budget., Enforce a minimum gap between separate searches. (+13 more)

### Community 8 - "test_mycareersfuture.py"
Cohesion: 0.10
Nodes (33): AsyncClient, _build_search_url(), _client(), _extract_skills(), _fetch_job_detail(), _format_employment_types(), _format_location(), _format_salary() (+25 more)

### Community 9 - "routes.py"
Cohesion: 0.10
Nodes (34): browser_capture(), browser_observe(), browser_refresh(), browser_replay(), cancel_task(), candidate_platforms(), create_browser_session(), decide_approval() (+26 more)

### Community 10 - "fastjobs.py"
Cohesion: 0.12
Nodes (30): _build_listing_url(), _check_blocker(), _clean_salary(), _connect(), _extract_job_detail(), _extract_jobs(), _extract_jobs_with_details(), _latest_session_row() (+22 more)

### Community 11 - "linkedin_people.py"
Cohesion: 0.10
Nodes (29): _build_search_url(), _check_blocker(), enrich_candidates(), _extract_candidates(), _extract_candidates_with_details(), _extract_profile_detail(), filter_by_location(), _filter_excluded() (+21 more)

### Community 12 - "background.js"
Cohesion: 0.19
Nodes (33): cmdClick(), cmdDiscoverFlow(), cmdExtract(), cmdFill(), cmdFindResultCard(), cmdGetCookies(), cmdLinkedinJobsSearch(), cmdLinkedinPeopleEnrich() (+25 more)

### Community 13 - "assess_credibility"
Cohesion: 0.11
Nodes (28): assess_credibility(), _avg_months(), CredibilityReport, _detect_title_inflation(), _duration_to_months(), _evidence_ratio(), parse_roles(), Any (+20 more)

### Community 14 - "test_source_flows.py"
Cohesion: 0.09
Nodes (34): build_boolean_keywords(), build_boolean_keywords_async(), compact_boolean_query(), Async variant: build keywords then LLM-compact if over the limit. `limit` lets…, Convert raw wizard events into (steps, card_selectors). mark_card events are…, Merge plan queries + excludes into ONE boolean string for a site's keyword box…, Compact a boolean keyword string via LLM to fit the 500-char limit enforced by…, templatize() (+26 more)

### Community 15 - "manifest.json"
Cohesion: 0.10
Nodes (19): action, default_icon, default_popup, default_title, background, service_worker, 128, 16 (+11 more)

### Community 16 - "schemas.py"
Cohesion: 0.13
Nodes (25): browser_takeover(), ActivityEvent, ApprovalRequest, BrowserSessionView, BrowserTakeoverRequest, CandidateMatchResult, JobMatchResult, JobSearchRequest (+17 more)

### Community 17 - "compilerOptions"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 18 - "WizardSession"
Cohesion: 0.11
Nodes (11): Live PNG of the wizard page (optionally clipped + upscaled). If the page still…, Find a QR code on the page and return (x, y, width, height) in CSS pixels,…, Type credentials into the best-matching fields on the current page., Submit an OTP/MFA code into the first visible short text input., A HEADLESS browser running inside the container; the user drives it remotely…, Click the page at screenshot coordinates (scaled to viewport)., Type into the currently-focused element (after a preview click). Full remote-…, Press a named key (Enter, Tab, Escape…) in the remote browser. (+3 more)

### Community 19 - "package.json"
Cohesion: 0.07
Nodes (25): metadata, dependencies, next, react, react-dom, description, devDependencies, @types/node (+17 more)

### Community 20 - "test_sourcing_plan.py"
Cohesion: 0.09
Nodes (28): CandidateSearchRequest, Normalized platform list (platforms[] or the legacy single platform), capped at…, A candidate sourcing plan. Mirrors the external system's analysis panel:…, asyncio, Sourcing-plan tests: schema caps, plan helpers, adapter plan logic, graph…, Sequential queries merge + dedupe + post-filters, enrich budget applied.…, The exact payload shape the external system sends (screenshot panel)., A source with a find_candidates flow of ANY status (even broken) is a valid… (+20 more)

### Community 21 - "cdp-proxy.py"
Cohesion: 0.13
Nodes (21): _envelope(), install_error_handlers(), Any, FastAPI, Unified error envelope for API consumers. All errors return a consistent shape:…, Register FastAPI exception handlers that emit the unified envelope., _request_id(), CDP Tunnel to Local Brave (+13 more)

### Community 22 - "tunnel.sh"
Cohesion: 0.31
Nodes (20): brave_cdp_ready(), cmd_brave(), cmd_start(), cmd_start_ngrok(), cmd_start_proxy(), cmd_status(), cmd_stop(), cmd_stop_one() (+12 more)

### Community 23 - "nodes.py"
Cohesion: 0.10
Nodes (29): _candidate_adapters(), _candidate_source_platforms(), _clean(), _flow_platforms(), _no_browser_session_available(), _noop_search(), _norm_text(), Any (+21 more)

### Community 24 - "Base"
Cohesion: 0.12
Nodes (22): Base, get_db(), AsyncSession, Database setup: SQLAlchemy async engine + session factory., Declarative base for all ORM models., FastAPI dependency yielding a database session., Approval, BrowserAction (+14 more)

### Community 25 - "source_flows.py"
Cohesion: 0.18
Nodes (18): discover_flow(), domain_of(), execute_flow(), _extract_page(), _looks_blocked(), _looks_logged_out(), page_keyboard_type(), Any (+10 more)

### Community 26 - "main.py"
Cohesion: 0.12
Nodes (24): get_task(), get_task_results(), get_task_results_compat(), AsyncSession, get, List past search tasks (most recent first), with result counts. Lets users…, Canonical task results endpoint., Compat shim for an external system's path convention. `opportunity_id` is the… (+16 more)

### Community 27 - "Career Agent System"
Cohesion: 0.16
Nodes (18): Career Agent System, GitHub Actions CI Workflow, Koyeb Deployment Guide, Career Agent Phase 1 Design Specification, Local Dev Infra Docker Compose, Steel Browser Docker Compose, Career Agent API Integration Guide, Find Candidates API Spec (+10 more)

### Community 28 - "agent.py"
Cohesion: 0.19
Nodes (15): agent_execute(), agent_poll(), agent_result(), agent_status(), AgentResult, AgentStatus, FlowExecuteRequest, Any (+7 more)

### Community 29 - "get_settings"
Cohesion: 0.31
Nodes (8): _get_key_store(), Request, API authentication and per-key rate limiting. API-key auth via the `X-API-Key`…, FastAPI dependency enforcing API-key auth + rate limiting. Returns the…, require_api_key(), get_settings(), X-API-Key Authentication, Per-Key Rate Limiting

### Community 30 - "test_candidates.py"
Cohesion: 0.13
Nodes (20): _extract_skills(), _normalize_flow_candidate(), Best-effort keyword extraction from a candidate search query. e.g. "Java,…, Map one raw flow-extracted row to the canonical candidate schema.…, _FakeFlow, _FakeFlowSource, Tests for candidate search helpers and scoring., A search/listing wrapper href (SEEK /talentsearch/keyword?...searchQuery=) must… (+12 more)

### Community 31 - "_apply_excludes"
Cohesion: 0.18
Nodes (10): Normalized query list (queries[] or the single query), capped at…, _apply_excludes(), Append LinkedIn's NOT (...) clause for the plan's exclude terms. Validated…, LinkedIn quirk (live A/B-verified): NOT clauses with 5+ terms return ZERO…, Long multi-OR base + NOT(...) makes LinkedIn serve empty pages — the clause…, test_apply_excludes_capped_at_max_not_terms(), test_apply_excludes_drops_not_clause_on_long_queries(), test_apply_excludes_none() (+2 more)

### Community 32 - "dedupe_candidates"
Cohesion: 0.33
Nodes (6): dedupe_candidates(), _normalize_profile_url(), Canonical form of a profile URL for dedupe (path only, no query/fragment). Live…, Merge results across queries by normalized profile URL. Keeps the first…, test_dedupe_candidates_counts_hits(), test_normalize_profile_url()

### Community 33 - "_gate_relaxed_rows"
Cohesion: 0.18
Nodes (11): _gate_relaxed_rows(), _matches_plan_groups(), _or_groups(), Parse a boolean query into its top-level AND-separated term groups. '("QC tech"…, True when the candidate card matches >=2 distinct plan groups. Used to gate…, Drop relaxed-pass rows that don't loosely match the plan structure. Returns…, Live regression: relaxed pass returned a lawyer for a QC+microarray plan., test_or_groups_parses_and_shape() (+3 more)

### Community 34 - "test_security.py"
Cohesion: 0.18
Nodes (5): client(), fixture, Tests for API security: auth + rate limiting + error envelope., test-key-2 has a limit of 3/min -> 4th request is 429., test_rate_limit_429()

### Community 35 - "config.py"
Cohesion: 0.18
Nodes (8): _parse_list(), Application configuration loaded from environment variables., Accept both JSON arrays and comma-separated strings for list fields., Runtime settings. Override via environment variables or .env file., Settings, LLM service using the Z.AI GLM coding-plan endpoint. The coding-plan…, Polite pacing service. Goal: behave like a human user so LinkedIn's anti-robot…, BaseSettings

### Community 36 - "test_sources_api.py"
Cohesion: 0.31
Nodes (8): client(), _headers(), fixture, Tests for pluggable source CRUD (no browser needed)., A bare word URL ("JobStreet") would navigate login to https://jobstreet/ —…, test_create_source_rejects_dotless_url(), test_source_crud_roundtrip(), test_wizard_start_unknown_source_404()

### Community 37 - "_first_or_group"
Cohesion: 0.25
Nodes (8): _broad_variants(), _first_or_group(), Extract the first parenthesized OR-group from a boolean query. '"agency…, Build maximally-broad LinkedIn queries from a strict plan. Live evidence…, Sparse merged results (< threshold) + OR-group -> relaxed run., test_broad_variants_strip_quotes_and_and_chains(), test_first_or_group_extraction(), test_relaxed_variant_triggers_below_threshold()

### Community 38 - "proxy_config"
Cohesion: 0.29
Nodes (6): proxy_config(), proxy_env(), Any, Proxy configuration for browser automation. When `PROXY_URL` is set, Playwright…, Return a Playwright `proxy` dict, or None if no proxy is configured. Playwright…, Return the subset of proxy env vars (for subprocess/child launches).

### Community 39 - "filter_excluded_results"
Cohesion: 0.50
Nodes (4): filter_excluded_results(), Post-filter results whose text mentions an excluded term. The NOT() clause in…, test_filter_excluded_results_drops_matches(), test_filter_excluded_results_no_excludes_noop()

### Community 40 - "sources.py"
Cohesion: 0.11
Nodes (29): AgentSessionPayload, BaseModel, API routes for pluggable sources: CRUD, guided wizard, flows., # NOTE: `from __future__ import annotations` makes `-> None` a string that, # NOTE: run_flow's step loop does NOT execute find_result_card, Type credentials into the visible login form (UI-driven sign-in)., Click at screenshot coordinates (for consent screens, cookies, etc.)., Type into the focused element (click a field in the preview first). (+21 more)

### Community 41 - "linkedin.py"
Cohesion: 0.11
Nodes (29): _build_search_url(), _cdp_headers(), _check_blocker(), _connect(), _connect_with_best_session(), _connect_with_session(), _extract_job_detail(), _extract_jobs() (+21 more)

### Community 42 - "handle_client"
Cohesion: 0.38
Nodes (6): auth_ok(), handle_client(), main(), Minimal HTTP CONNECT proxy that egresses through the Mac's residential IP. This…, StreamReader, StreamWriter

### Community 43 - "SlidingWindowLimiter"
Cohesion: 0.40
Nodes (3): Per-key sliding-window rate limiter (in-memory)., Check if the key is within limit. Returns (allowed, retry_after_s)., SlidingWindowLimiter

### Community 48 - "SourceFlow"
Cohesion: 0.15
Nodes (21): agent_session(), agent_session_store(), create_source(), list_sources(), Update a source: enable/disable, rename, or repoint its base_url. base_url…, Store cookies captured by the extension after a manual login. Stored as a…, Store cookies captured by the extension after a manual login., _source_view() (+13 more)

### Community 59 - "LLMService"
Cohesion: 0.13
Nodes (12): LLMService, LLM rerank of jobs against the career profile. Preserves the deterministic…, LLM rerank of candidates against a search criteria / job reference. Uses the…, Parse the LLM's JSON response, tolerating markdown fences., Recover complete records from a JSON array cut mid-record., Thin client for the Z.AI coding-plan LLM endpoint., Send a chat request. Returns the text response or None on error., Z.AI Coding-Plan Headers (+4 more)

### Community 61 - "_agent_discover"
Cohesion: 0.22
Nodes (15): _agent_discover(), Extension-driven discovery for non-LinkedIn sites. For seek: run the known…, _FakeRegistry, _FakeReq, _FakeSource, Tests for _agent_discover SEEK record path (URL-param search, no DOM fill)., run_flow reports needs_human → 502 mentions re-login; no card detection., All probes miss → 502 message includes page title/bodyChars from page_state so… (+7 more)

### Community 62 - "test_llm_rerank.py"
Cohesion: 0.17
Nodes (12): _FakeRegistry, asyncio, Exception, fixture, Tests for LLM rerank JSON salvage and LinkedIn agent-busy skip., _record(), svc(), test_busy_dispatch_skips_leg() (+4 more)

### Community 63 - "test_compat_routes.py"
Cohesion: 0.30
Nodes (11): Candidate, MatchEvaluation, client(), _compat_paths(), _headers(), fixture, Tests for the external-candidates compat routes (Expressautomate-style paths)., _seed_completed_task_with_results() (+3 more)

### Community 64 - "EmbeddingProvider"
Cohesion: 0.20
Nodes (7): EmbeddingProvider, NoopReranker, Any, Interface for embedding similarity (Qdrant-backed in production)., Interface for LLM-based reranking., Reranker, Protocol

### Community 65 - "BrowserError"
Cohesion: 0.22
Nodes (8): BrowserError, Exception, Raised when a browser operation fails., Launch (or connect to) a browser and open a fresh page., A platform raising BrowserError mid-loop (extension agent offline) must not…, When the only platform goes offline and nothing was collected, the run still…, test_midloop_browsererror_keeps_partial_results(), test_offline_only_still_pauses_with_actionable_message()

### Community 66 - "deduplicate"
Cohesion: 0.29
Nodes (8): deduplicate(), DEDUPLICATE: drop duplicates on (source, name), preferring deep links, then a…, _cand(), Regression: candidate rows have no title/company keys, so the fuzzy pass keyed…, test_deduplicate_collapses_exact_name_dupes_preferring_deep_link(), test_deduplicate_collapses_seek_search_and_profile_rows(), test_deduplicate_keeps_distinct_candidates(), test_fuzzy_dedupe_collapses_cross_source()

### Community 67 - "Career-agent flow recording: SEEK/jobstreet selectors"
Cohesion: 0.25
Nodes (7): Career-agent flow recording: SEEK/jobstreet selectors, Diagnostics that worked (in order), Fix, Known residual (career-agent repo, unfixed), Root cause, Symptom, Verification

### Community 68 - "SourceFlowView"
Cohesion: 0.29
Nodes (7): list_flows(), get, Live PNG of the wizard browser. The UI polls this for a live view. zoom=page —…, update_flow(), wizard_screenshot(), SourceFlowView, patch

### Community 69 - "browser.py"
Cohesion: 0.29
Nodes (6): ElementRef, ObserveResult, Browser service layer. Phase 1 implementation drives a local/remote Chromium…, A lightweight, stable reference to a page element for click/type., Persistent Browser Session Design, Steel Cloud Browser Runtime

### Community 70 - "_MultiQueryFakeRegistry"
Cohesion: 0.29
Nodes (4): _MultiQueryFakeRegistry, Records run_flow dispatches; returns canned results per query., Each plan query gets its OWN run_flow dispatch (parity with LinkedIn's per-…, test_flow_search_runs_per_query_legs()

### Community 73 - "Runbook: Package a production OpenCode desktop app"
Cohesion: 0.33
Nodes (5): Context, Correct chain (both steps, in order, same channel), Extra content check (feature verification), Install / relaunch, Runbook: Package a production OpenCode desktop app

### Community 74 - "Career-bot revised spec (2026-09-02): integration diff checklist"
Cohesion: 0.40
Nodes (4): Career-bot revised spec (2026-09-02): integration diff checklist, Expressautomate-side conformance state, The path trap (cost ~20 min on 2026-09-02), What the revised spec changed (2026-09-02)

### Community 75 - "_restore_stripped_api_prefix"
Cohesion: 0.50
Nodes (4): Any, Request, _restore_stripped_api_prefix(), middleware

### Community 76 - "_connect"
Cohesion: 0.50
Nodes (4): _cdp_headers(), _connect(), Connect Playwright to the authenticated Brave session via CDP., Parse CDP_AUTH_HEADER ('Name: Value') into a headers dict, or None.

### Community 77 - "Disable V1 snapshot event persistence for local builds"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Disable V1 snapshot event persistence for local builds

### Community 78 - "Pluggable job-source architecture (guided wizard)"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Pluggable job-source architecture (guided wizard)

### Community 79 - "Expired source session UX (pause + re-login banner)"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Expired source session UX (pause + re-login banner)

### Community 80 - "Source session expiry handling"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Source session expiry handling

### Community 81 - "Wizard runs fully server-side (no local tunnel)"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Wizard runs fully server-side (no local tunnel)

### Community 82 - "Default idle stall guard tuned to 180s"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Default idle stall guard tuned to 180s

### Community 83 - "Provider idle stall guard is default-on"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Provider idle stall guard is default-on

### Community 84 - "Fork release process and tag-creation retry"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Fork release process and tag-creation retry

### Community 85 - "Fork release process (fork.6 pattern)"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Fork release process (fork.6 pattern)

### Community 86 - "Fork releases use 'OpenCode Desktop — <feature>' titles"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Fork releases use 'OpenCode Desktop — <feature>' titles

### Community 87 - "Career bot prod browser access — HARD USER CONSTRAINTS (2026-09-01): NEVER propose or implement a tunnel (ngrok/cdp-proxy/ngrok-style) again; user forbade it permanently and forcefully. Also rejected: background launchd daemons, dedicated automation browser/profile, headless-on-Koyeb pitches (unvalidated). Only acceptable direction: drive the user's own already-logged-in browser from INSIDE the browser (existing extension + /v1/agent/execute|poll|result protocol); no debug flags, no external reachability requirements. Extraction logic (boolean queries, NOT excludes, card parsing) is proven; only the transport/trigger needs porting to the extension."
Cohesion: 0.50
Nodes (3): Career bot prod browser access — HARD USER CONSTRAINTS (2026-09-01): NEVER propose or implement a tunnel (ngrok/cdp-proxy/ngrok-style) again; user forbade it permanently and forcefully. Also rejected: background launchd daemons, dedicated automation browser/profile, headless-on-Koyeb pitches (unvalidated). Only acceptable direction: drive the user's own already-logged-in browser from INSIDE the browser (existing extension + /v1/agent/execute|poll|result protocol); no debug flags, no external reachability requirements. Extraction logic (boolean queries, NOT excludes, card parsing) is proven; only the transport/trigger needs porting to the extension., Context, Decision / rationale

### Community 88 - "Desktop build: channel + step order"
Cohesion: 0.50
Nodes (3): Desktop build: channel + step order, Root cause / fix, What happened

### Community 89 - "COUNT(*) quotas cannot bound upsert endpoints"
Cohesion: 0.50
Nodes (3): COUNT(*) quotas cannot bound upsert endpoints, Root cause / fix, What happened

### Community 90 - "DeepInfra LLM calls need longer timeout than Cerebras"
Cohesion: 0.50
Nodes (3): DeepInfra LLM calls need longer timeout than Cerebras, Root cause / fix, What happened

### Community 91 - "Koyeb env updates collide with in-flight CI deploys"
Cohesion: 0.50
Nodes (3): Koyeb env updates collide with in-flight CI deploys, Root cause / fix, What happened

### Community 92 - "LLM-paying jobs need attempt caps at claim time"
Cohesion: 0.50
Nodes (3): LLM-paying jobs need attempt caps at claim time, Root cause / fix, What happened

### Community 93 - "Per-selector :not() chains explode generated CSS size"
Cohesion: 0.50
Nodes (3): Per-selector :not() chains explode generated CSS size, Root cause / fix, What happened

### Community 94 - "Modal + vLLM serving gotchas"
Cohesion: 0.50
Nodes (3): Modal + vLLM serving gotchas, Root cause / fix, What happened

### Community 95 - "Modal web_server hangs on vLLM streaming — use @app.server instead"
Cohesion: 0.50
Nodes (3): Modal web_server hangs on vLLM streaming — use @app.server instead, Root cause / fix, What happened

### Community 96 - "Qwen3.8-27B on Modal: two root causes (web_server streaming + MTP-3 crash)"
Cohesion: 0.50
Nodes (3): Qwen3.8-27B on Modal: two root causes (web_server streaming + MTP-3 crash), Root cause / fix, What happened

### Community 97 - "vLLM tool-call parser for Qwen3.5-family models"
Cohesion: 0.50
Nodes (3): Root cause / fix, vLLM tool-call parser for Qwen3.5-family models, What happened

### Community 98 - "Cache-efficiency key reads 0% with self-hosted models"
Cohesion: 0.50
Nodes (3): Cache-efficiency key reads 0% with self-hosted models, Root cause / fix, What happened

### Community 99 - "opencode config: model vision declared with 'capabilities' key is silently ignored"
Cohesion: 0.50
Nodes (3): opencode config: model vision declared with 'capabilities' key is silently ignored, Root cause / fix, What happened

### Community 100 - "Confounded A/B: different output lengths + wrong log file led to wrong 'steps 3 wins' conclusion"
Cohesion: 0.50
Nodes (3): Confounded A/B: different output lengths + wrong log file led to wrong 'steps 3 wins' conclusion, Root cause / fix, What happened

### Community 101 - "DFlash 2 open-PR audit: watch #32052 (GDN state commit + extra_buffer radix)"
Cohesion: 0.50
Nodes (3): DFlash 2 open-PR audit: watch #32052 (GDN state commit + extra_buffer radix), Root cause / fix, What happened

### Community 102 - "GPU instance lifecycle: caches die with instances; always stage launch scripts + know your rebuild path"
Cohesion: 0.50
Nodes (3): GPU instance lifecycle: caches die with instances; always stage launch scripts + know your rebuild path, Root cause / fix, What happened

### Community 103 - "OpenCode custom-provider vision: use modalities.input not capabilities.input"
Cohesion: 0.50
Nodes (3): OpenCode custom-provider vision: use modalities.input not capabilities.input, Root cause / fix, What happened

### Community 104 - "Runbook lagged live config on chunked-prefill"
Cohesion: 0.50
Nodes (3): Root cause / fix, Runbook lagged live config on chunked-prefill, What happened

### Community 105 - "Shell: outer truncate-redirect clobbers inner appends to the same log file"
Cohesion: 0.50
Nodes (3): Root cause / fix, Shell: outer truncate-redirect clobbers inner appends to the same log file, What happened

### Community 106 - "Subagents must not pin a model in frontmatter if they should inherit the primary agent's model"
Cohesion: 0.50
Nodes (3): Root cause / fix, Subagents must not pin a model in frontmatter if they should inherit the primary agent's model, What happened

### Community 107 - "User protocol: no unsolicited advice, execute instructions only"
Cohesion: 0.50
Nodes (3): Root cause / fix, User protocol: no unsolicited advice, execute instructions only, What happened

### Community 108 - "vast.ai launch-mode quirks (4 failures to 1 success)"
Cohesion: 0.50
Nodes (3): Root cause / fix, vast.ai launch-mode quirks (4 failures to 1 success), What happened

### Community 109 - "vast/RunPod template: never pip-install SGLang from a moving PR ref"
Cohesion: 0.50
Nodes (3): Root cause / fix, vast/RunPod template: never pip-install SGLang from a moving PR ref, What happened

### Community 110 - "vastai update template wipes unspecified fields + needs hash id"
Cohesion: 0.50
Nodes (3): Root cause / fix, vastai update template wipes unspecified fields + needs hash id, What happened

### Community 111 - "Duplicate user messages: prompt persists before joining run"
Cohesion: 0.50
Nodes (3): Duplicate user messages: prompt persists before joining run, Root cause / fix, What happened

### Community 112 - "Self-hosted vLLM 0% cache hit rate is server-side, not opencode"
Cohesion: 0.50
Nodes (3): Root cause / fix, Self-hosted vLLM 0% cache hit rate is server-side, not opencode, What happened

### Community 113 - "syv-ai 381tok/s stack is Ampere+patched-vLLM only, not portable to sm120"
Cohesion: 0.50
Nodes (3): Root cause / fix, syv-ai 381tok/s stack is Ampere+patched-vLLM only, not portable to sm120, What happened

### Community 114 - "Upstream opencode sync: cherry-pick workflow + fork-local quirks"
Cohesion: 0.50
Nodes (3): Root cause / fix, Upstream opencode sync: cherry-pick workflow + fork-local quirks, What happened

### Community 115 - "tmp profile copy gets wiped by macOS"
Cohesion: 0.50
Nodes (3): Root cause / fix, tmp profile copy gets wiped by macOS, What happened

### Community 116 - "MV3 extension — content script single-shot GET_STATE dies until reload"
Cohesion: 0.50
Nodes (3): MV3 extension — content script single-shot GET_STATE dies until reload, Root cause / fix, What happened

### Community 117 - "Bitdeer API rejects suffixed model IDs"
Cohesion: 0.50
Nodes (3): Bitdeer API rejects suffixed model IDs, Root cause / fix, What happened

### Community 118 - "CFPreferences dict values as NSString break Swift as?-cast — the resurfacing 'settings gone' master cause"
Cohesion: 0.50
Nodes (3): CFPreferences dict values as NSString break Swift as?-cast — the resurfacing 'settings gone' master cause, Root cause / fix, What happened

### Community 119 - "opencode.json custom model needs attachment:true for images"
Cohesion: 0.50
Nodes (3): opencode.json custom model needs attachment:true for images, Root cause / fix, What happened

### Community 120 - "Vast 6000 template switches SGLang+DFlash2 to vLLM MTP-2 unsloth-NVFP4"
Cohesion: 0.50
Nodes (3): Context, Decision / rationale, Vast 6000 template switches SGLang+DFlash2 to vLLM MTP-2 unsloth-NVFP4

### Community 121 - "vast.ai template onstart 16384-char cap breaks base64 scripts"
Cohesion: 0.50
Nodes (3): Root cause / fix, vast.ai template onstart 16384-char cap breaks base64 scripts, What happened

### Community 122 - "Brave extensions deadlock Playwright connectOverCDP"
Cohesion: 0.50
Nodes (3): Brave extensions deadlock Playwright connectOverCDP, Root cause / fix, What happened

### Community 123 - "Chrome policy force-install is not a novice install path"
Cohesion: 0.50
Nodes (3): Chrome policy force-install is not a novice install path, Root cause / fix, What happened

### Community 124 - "Wizard headed-browser 502 on Koyeb"
Cohesion: 0.50
Nodes (3): Root cause / fix, What happened, Wizard headed-browser 502 on Koyeb

### Community 125 - "npm plugin entry resolution + Node ESM plugin imports"
Cohesion: 0.50
Nodes (3): npm plugin entry resolution + Node ESM plugin imports, Root cause / fix, What happened

### Community 126 - "opencode model config: 3 conflicting layers silently fell back to glm-4.7"
Cohesion: 0.50
Nodes (3): opencode model config: 3 conflicting layers silently fell back to glm-4.7, Root cause / fix, What happened

### Community 127 - "Brave CDP wedges after repeated Playwright connect_over_cdp cycles (each browser-level attach re-enumerates all targets; endpoint degrades ~10-15 min after launch; ws handshake times out even at 180s). FIX: hold ONE connection per plan — career bot linkedin_people.py _PlanSession (lazy connect thunk = _connect_with_best_session; reuse the SAME page it returns; the pair is (playwright_manager, page) and the manager has NO .contexts). Set connect timeout 15s fail-fast. Also: pkill -f 'uvicorn app.main' before backend restart or stale code serves; results endpoint is GET /api/v1/tasks/{id}/results (no /search prefix). E2E verified: task 8f6d1074 completed 210s, 9 candidates, plan_detail 'query3: 10 -> 9 unique'."
Cohesion: 0.50
Nodes (3): Brave CDP wedges after repeated Playwright connect_over_cdp cycles (each browser-level attach re-enumerates all targets; endpoint degrades ~10-15 min after launch; ws handshake times out even at 180s). FIX: hold ONE connection per plan — career bot linkedin_people.py _PlanSession (lazy connect thunk = _connect_with_best_session; reuse the SAME page it returns; the pair is (playwright_manager, page) and the manager has NO .contexts). Set connect timeout 15s fail-fast. Also: pkill -f 'uvicorn app.main' before backend restart or stale code serves; results endpoint is GET /api/v1/tasks/{id}/results (no /search prefix). E2E verified: task 8f6d1074 completed 210s, 9 candidates, plan_detail 'query3: 10 -> 9 unique'., Root cause / fix, What happened

### Community 128 - "Koyeb prod career-agent API: Koyeb route rule strips '/api' prefix, AND the FastAPI router itself carries prefix '/api/v1' — so prod paths are DOUBLED: POST https://career-agent-kianwoon-88223cd5.koyeb.app/api/v1/api/v1/search/candidates (same for /tasks/{id}/results). Prod API key lives in Koyeb env API_KEYS (career_...:60), NOT dev-e2e-key. Tunnel chain (Brave:9222 -> cdp-proxy:9999 -> ngrok -> Koyeb) is restored via ./tunnel.sh start (auto-updates Koyeb BRAVE_CDP_URL env) + ./tunnel.sh verify; Brave must be launched with --remote-debugging-port=9222 --remote-allow-origins='*' --disable-extensions (extensions deadlock Playwright CDP attach per docs/CDP-TUNNEL-RUNBOOK.md). Verified: prod task 942db35d completed 165s, 9 candidates via tunnel."
Cohesion: 0.50
Nodes (3): Koyeb prod career-agent API: Koyeb route rule strips '/api' prefix, AND the FastAPI router itself carries prefix '/api/v1' — so prod paths are DOUBLED: POST https://career-agent-kianwoon-88223cd5.koyeb.app/api/v1/api/v1/search/candidates (same for /tasks/{id}/results). Prod API key lives in Koyeb env API_KEYS (career_...:60), NOT dev-e2e-key. Tunnel chain (Brave:9222 -> cdp-proxy:9999 -> ngrok -> Koyeb) is restored via ./tunnel.sh start (auto-updates Koyeb BRAVE_CDP_URL env) + ./tunnel.sh verify; Brave must be launched with --remote-debugging-port=9222 --remote-allow-origins='*' --disable-extensions (extensions deadlock Playwright CDP attach per docs/CDP-TUNNEL-RUNBOOK.md). Verified: prod task 942db35d completed 165s, 9 candidates via tunnel., Root cause / fix, What happened

## Knowledge Gaps
- **226 isolated node(s):** `_FakeFlowSource`, `_FakeFlow`, `entrypoint.sh script`, `BRAVE_CDP_URL`, `career-agent-backend` (+221 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 681 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BrowserError` connect `BrowserError` to `BrowserSession`, `browser.py`, `BrowserService`, `routes.py`, `fastjobs.py`, `linkedin.py`, `linkedin_people.py`, `_connect`, `schemas.py`, `nodes.py`, `test_candidates.py`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Why does `AgentRegistry` connect `AgentRegistry` to `test_sourcing_plan.py`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Why does `install_error_handlers()` connect `cdp-proxy.py` to `main.py`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `BrowserError` (e.g. with `run_search()` and `browser_observe()`) actually correct?**
  _`BrowserError` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `Base` (e.g. with `lifespan()` and `create_all()`) actually correct?**
  _`Base` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `_FakeFlowSource`, `_FakeFlow`, `entrypoint.sh script` to the rest of the system?**
  _226 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `api.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.05192107995846314 - nodes in this community are weakly interconnected._