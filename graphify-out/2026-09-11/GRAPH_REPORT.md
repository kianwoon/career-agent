# Graph Report - career bot  (2026-09-05)

## Corpus Check
- 135 files · ~85,719 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1186 nodes · 2381 edges · 61 communities (44 shown, 15 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 165 edges (avg confidence: 0.93)
- Token cost: 98,000 input · 9,000 output

## Community Hubs (Navigation)
- Frontend Search UI
- Source Setup API
- Browser Runtime Service
- LLM Reranking and Matching
- Agent Workflow Orchestration
- Session Encryption Capture
- Extension Command Relay
- Cache and Pacing
- MyCareersFuture Job Adapter
- Task API Routes
- FastJobs Job Adapter
- LinkedIn People Search
- Extension Background Commands
- Credibility Assessment
- Flow Recording Templatization
- Extension Manifest Config
- Approval Workflow Schemas
- Frontend TypeScript Config
- Wizard Session State
- Frontend Package Dependencies
- Sourcing Plan Tests
- API Error Envelope
- CDP Tunnel Scripts
- Browser Session Endpoints
- Database ORM Models
- Recorded Flow Execution
- FastAPI App Startup
- Project Documentation
- Agent Relay Endpoints
- API Key Authentication
- Candidate Normalization Tests
- Query Plan Soft Caps
- Candidate Dedup Filtering
- Relevance Gate Filtering
- API Security Tests
- Backend Config Settings
- Sources API Tests
- Query Broadening Variants
- Proxy Configuration
- Excluded Results Filtering
- Database Session Management
- LinkedIn Plan Orchestration Tests
- HTTP Debug Proxy
- Sliding Window Rate Limiter
- Extension Popup UI
- Runtime Config Endpoint
- Backend Container Entrypoint
- Extension Packaging Script
- Frontend App Layout
- Frontend Auth Middleware
- Next.js Configuration
- Backend Package Init
- Wizard Capture State
- Cookie sameSite Sanitization
- Candidate Search Concept
- Job Search Concept
- Qdrant Vector Store
- Redis Task State
- Backend Package Metadata

## God Nodes (most connected - your core abstractions)
1. `Home()` - 42 edges
2. `resolveApiBase()` - 34 edges
3. `addEvent()` - 25 edges
4. `Base` - 23 edges
5. `WizardSession` - 23 edges
6. `AgentState` - 22 edges
7. `postJson()` - 21 edges
8. `SourceFlow` - 20 edges
9. `SearchType` - 20 edges
10. `BrowserError` - 19 edges

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

## Communities (61 total, 15 thin omitted)

### Community 0 - "Frontend Search UI"
Cohesion: 0.05
Nodes (98): addEvent(), ConnectBrowserAgent(), Home(), addEventPrev(), clearWizPolls(), ensureSession(), handleAddSource(), handleAgentCaptureSession() (+90 more)

### Community 1 - "Source Setup API"
Cohesion: 0.06
Nodes (82): _agent_discover(), agent_login(), agent_record(), agent_record_manual_start(), agent_record_manual_stop(), agent_session(), agent_session_capture(), agent_session_store() (+74 more)

### Community 2 - "Browser Runtime Service"
Cohesion: 0.05
Nodes (42): BrowserError, BrowserService, BrowserSession, ElementRef, ObserveResult, Any, Browser service layer. Phase 1 implementation drives a local/remote Chromium…, Extract structured data per a schema of field name -> selector. (+34 more)

### Community 3 - "LLM Reranking and Matching"
Cohesion: 0.06
Nodes (43): Evidence, MatchResult, A single piece of traceable evidence backing a match score., A ranked job or candidate with score and supporting evidence., LLMService, LLM service using the Z.AI GLM coding-plan endpoint. The coding-plan…, LLM rerank of jobs against the career profile. Preserves the deterministic…, LLM rerank of candidates against a search criteria / job reference. Uses the… (+35 more)

### Community 4 - "Agent Workflow Orchestration"
Cohesion: 0.10
Nodes (42): build_graph(), LangGraph supervisor graph definition., Build the Phase 1 supervisor graph., AgentState, _candidate_adapters(), check_human(), deduplicate(), extract() (+34 more)

### Community 5 - "Session Encryption Capture"
Cohesion: 0.10
Nodes (34): decrypt_session_state(), _derive_key(), encrypt_session_state(), _get_key(), Encrpytion service for browser session state (cookies + localStorage). Uses…, Get the 32-byte AES key from config or derive a dev key., Encrypt a JSON string (Playwright storage_state) into a base64 blob. Format:…, Decrypt a base64 blob back into the original JSON string. Returns the raw… (+26 more)

### Community 6 - "Extension Command Relay"
Cohesion: 0.08
Nodes (26): AgentRegistry, Command, Any, Browser-extension agent relay — HTTP polling edition. The MV3 service worker +…, Enqueue a command and await the extension's result. lock_wait_s bounds the wait…, Extension asks for the next command (oldest first)., Command queue + result store for the polling extension agent., Record the polling worker instance; fail orphaned commands. A worker reload… (+18 more)

### Community 7 - "Cache and Pacing"
Cohesion: 0.07
Nodes (21): Any, QueryCache, Simple in-memory result cache to avoid re-hitting LinkedIn for the same query.…, LRU cache keyed by (query, location) with time-based expiry., PacingService, Any, Enforce max pages per minute. Sleep if we're over budget., Enforce a minimum gap between separate searches. (+13 more)

### Community 8 - "MyCareersFuture Job Adapter"
Cohesion: 0.10
Nodes (33): AsyncClient, _build_search_url(), _client(), _extract_skills(), _fetch_job_detail(), _format_employment_types(), _format_location(), _format_salary() (+25 more)

### Community 9 - "Task API Routes"
Cohesion: 0.13
Nodes (34): _flow_platforms(), Names of enabled sources that have an active find_candidates flow. These are…, cancel_task(), candidate_platforms(), get_task(), get_task_results(), health(), AsyncSession (+26 more)

### Community 10 - "FastJobs Job Adapter"
Cohesion: 0.10
Nodes (33): _build_listing_url(), _check_blocker(), _clean_salary(), _connect(), _extract_job_detail(), _extract_jobs(), _extract_jobs_with_details(), _latest_session_row() (+25 more)

### Community 11 - "LinkedIn People Search"
Cohesion: 0.10
Nodes (30): _build_search_url(), _cdp_headers(), _check_blocker(), _connect(), enrich_candidates(), _extract_candidates(), _extract_candidates_with_details(), _extract_profile_detail() (+22 more)

### Community 12 - "Extension Background Commands"
Cohesion: 0.19
Nodes (32): cmdClick(), cmdDiscoverFlow(), cmdExtract(), cmdFill(), cmdFindResultCard(), cmdGetCookies(), cmdLinkedinJobsSearch(), cmdLinkedinPeopleEnrich() (+24 more)

### Community 13 - "Credibility Assessment"
Cohesion: 0.11
Nodes (28): assess_credibility(), _avg_months(), CredibilityReport, _detect_title_inflation(), _duration_to_months(), _evidence_ratio(), parse_roles(), Any (+20 more)

### Community 14 - "Flow Recording Templatization"
Cohesion: 0.10
Nodes (30): build_boolean_keywords(), build_boolean_keywords_async(), compact_boolean_query(), Convert raw wizard events into (steps, card_selectors). mark_card events are…, Merge plan queries + excludes into ONE boolean string for a site's keyword box…, Compact a boolean keyword string via LLM to fit the 500-char limit enforced by…, Async variant: build keywords then LLM-compact if over the limit., templatize() (+22 more)

### Community 15 - "Extension Manifest Config"
Cohesion: 0.07
Nodes (29): action, default_icon, default_popup, default_title, background, service_worker, 128, 16 (+21 more)

### Community 16 - "Approval Workflow Schemas"
Cohesion: 0.12
Nodes (26): decide_approval(), Record an approval decision. Phase 1 stores no pending approvals yet; this…, ApprovalDecision, ApprovalRequest, BrowserTakeoverRequest, CandidateMatchResult, JobMatchResult, MatchRole (+18 more)

### Community 17 - "Frontend TypeScript Config"
Cohesion: 0.07
Nodes (26): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+18 more)

### Community 18 - "Wizard Session State"
Cohesion: 0.12
Nodes (10): Live PNG of the wizard page (optionally clipped + upscaled). If the page still…, Find a QR code on the page and return (x, y, width, height) in CSS pixels,…, Type credentials into the best-matching fields on the current page., Submit an OTP/MFA code into the first visible short text input., A HEADLESS browser running inside the container; the user drives it remotely…, Click the page at screenshot coordinates (scaled to viewport)., Press a named key (Enter, Tab, Escape…) in the remote browser., Scroll the remote page (wheel) at screenshot coordinates. (+2 more)

### Community 19 - "Frontend Package Dependencies"
Cohesion: 0.08
Nodes (24): dependencies, next, react, react-dom, description, devDependencies, @types/node, @types/react (+16 more)

### Community 20 - "Sourcing Plan Tests"
Cohesion: 0.13
Nodes (19): CandidateSearchRequest, Normalized platform list (platforms[] or the legacy single platform), capped at…, A candidate sourcing plan. Mirrors the external system's analysis panel:…, Sourcing-plan tests: schema caps, plan helpers, adapter plan logic, graph…, The exact payload shape the external system sends (screenshot panel)., Oversized platform lists are truncated, not rejected (external callers must not…, Oversized queries/excludes are truncated, not rejected., _route_plan_payload() (+11 more)

### Community 21 - "API Error Envelope"
Cohesion: 0.14
Nodes (20): _envelope(), install_error_handlers(), Any, FastAPI, Unified error envelope for API consumers. All errors return a consistent shape:…, Register FastAPI exception handlers that emit the unified envelope., _request_id(), CDP Tunnel to Local Brave (+12 more)

### Community 22 - "CDP Tunnel Scripts"
Cohesion: 0.31
Nodes (20): brave_cdp_ready(), cmd_brave(), cmd_start(), cmd_start_ngrok(), cmd_start_proxy(), cmd_status(), cmd_stop(), cmd_stop_one() (+12 more)

### Community 23 - "Browser Session Endpoints"
Cohesion: 0.19
Nodes (19): browser_capture(), browser_observe(), browser_refresh(), browser_replay(), browser_takeover(), create_browser_session(), _default_user_id(), post (+11 more)

### Community 24 - "Database ORM Models"
Cohesion: 0.18
Nodes (16): Base, Declarative base for all ORM models., Approval, BrowserAction, CareerProfile, Company, Evidence, JobDescription (+8 more)

### Community 25 - "Recorded Flow Execution"
Cohesion: 0.18
Nodes (17): discover_flow(), domain_of(), execute_flow(), _extract_page(), _looks_logged_out(), page_keyboard_type(), Any, _quote_term() (+9 more)

### Community 26 - "FastAPI App Startup"
Cohesion: 0.13
Nodes (16): _configure_handlers(), lifespan(), Any, FastAPI, get, Request, Career Agent FastAPI application., Attach a stream handler to our loggers so INFO logs are visible. (+8 more)

### Community 27 - "Project Documentation"
Cohesion: 0.15
Nodes (18): Career Agent System, GitHub Actions CI Workflow, Koyeb Deployment Guide, Career Agent Phase 1 Design Specification, Local Dev Infra Docker Compose, Steel Browser Docker Compose, Career Agent API Integration Guide, Find Candidates API Spec (+10 more)

### Community 28 - "Agent Relay Endpoints"
Cohesion: 0.19
Nodes (15): agent_execute(), agent_poll(), agent_result(), agent_status(), AgentResult, AgentStatus, FlowExecuteRequest, Any (+7 more)

### Community 29 - "API Key Authentication"
Cohesion: 0.16
Nodes (10): APIKeyStore, _get_key_store(), Request, API authentication and per-key rate limiting. API-key auth via the `X-API-Key`…, Parsed API keys with per-key rate limits., FastAPI dependency enforcing API-key auth + rate limiting. Returns the…, require_api_key(), get_settings() (+2 more)

### Community 30 - "Candidate Normalization Tests"
Cohesion: 0.18
Nodes (14): _extract_skills(), _normalize_flow_candidate(), Best-effort keyword extraction from a candidate search query. e.g. "Java,…, Map one raw flow-extracted row to the canonical candidate schema.…, Tests for candidate search helpers and scoring., SEEK cards without links return the whole card as one camelCase-glued blob…, test_deduplicate_collapses_seek_search_and_profile_rows(), test_extract_skills_from_query() (+6 more)

### Community 31 - "Query Plan Soft Caps"
Cohesion: 0.18
Nodes (10): Normalized query list (queries[] or the single query), capped at…, _apply_excludes(), Append LinkedIn's NOT (...) clause for the plan's exclude terms. Validated…, LinkedIn quirk (live A/B-verified): NOT clauses with 5+ terms return ZERO…, Long multi-OR base + NOT(...) makes LinkedIn serve empty pages — the clause…, test_apply_excludes_capped_at_max_not_terms(), test_apply_excludes_drops_not_clause_on_long_queries(), test_apply_excludes_none() (+2 more)

### Community 32 - "Candidate Dedup Filtering"
Cohesion: 0.20
Nodes (11): dedupe_candidates(), _filter_excluded(), _normalize_profile_url(), Canonical form of a profile URL for dedupe (path only, no query/fragment). Live…, Merge results across queries by normalized profile URL. Keeps the first…, Run a sourcing plan against LinkedIn people search. Plan semantics (all…, Drop candidates whose headline/current_role mention an excluded term. The…, search_linkedin_people() (+3 more)

### Community 33 - "Relevance Gate Filtering"
Cohesion: 0.18
Nodes (11): _gate_relaxed_rows(), _matches_plan_groups(), _or_groups(), Parse a boolean query into its top-level AND-separated term groups. '("QC tech"…, True when the candidate card matches >=2 distinct plan groups. Used to gate…, Drop relaxed-pass rows that don't loosely match the plan structure. Returns…, Live regression: relaxed pass returned a lawyer for a QC+microarray plan., test_or_groups_parses_and_shape() (+3 more)

### Community 34 - "API Security Tests"
Cohesion: 0.18
Nodes (5): client(), fixture, Tests for API security: auth + rate limiting + error envelope., test-key-2 has a limit of 3/min -> 4th request is 429., test_rate_limit_429()

### Community 35 - "Backend Config Settings"
Cohesion: 0.22
Nodes (7): _parse_list(), Application configuration loaded from environment variables., Accept both JSON arrays and comma-separated strings for list fields., Runtime settings. Override via environment variables or .env file., Settings, Polite pacing service. Goal: behave like a human user so LinkedIn's anti-robot…, BaseSettings

### Community 36 - "Sources API Tests"
Cohesion: 0.31
Nodes (8): client(), _headers(), fixture, Tests for pluggable source CRUD (no browser needed)., A bare word URL ("JobStreet") would navigate login to https://jobstreet/ —…, test_create_source_rejects_dotless_url(), test_source_crud_roundtrip(), test_wizard_start_unknown_source_404()

### Community 37 - "Query Broadening Variants"
Cohesion: 0.25
Nodes (8): _broad_variants(), _first_or_group(), Extract the first parenthesized OR-group from a boolean query. '"agency…, Build maximally-broad LinkedIn queries from a strict plan. Live evidence…, Sparse merged results (< threshold) + OR-group -> relaxed run., test_broad_variants_strip_quotes_and_and_chains(), test_first_or_group_extraction(), test_relaxed_variant_triggers_below_threshold()

### Community 38 - "Proxy Configuration"
Cohesion: 0.29
Nodes (6): proxy_config(), proxy_env(), Any, Proxy configuration for browser automation. When `PROXY_URL` is set, Playwright…, Return a Playwright `proxy` dict, or None if no proxy is configured. Playwright…, Return the subset of proxy env vars (for subprocess/child launches).

### Community 39 - "Excluded Results Filtering"
Cohesion: 0.33
Nodes (6): Run a candidate search on a custom source via its recorded flow. Lets any…, _search_candidates_via_flow(), filter_excluded_results(), Post-filter results whose text mentions an excluded term. The NOT() clause in…, test_filter_excluded_results_drops_matches(), test_filter_excluded_results_no_excludes_noop()

### Community 40 - "Database Session Management"
Cohesion: 0.33
Nodes (5): get_db(), AsyncSession, Database setup: SQLAlchemy async engine + session factory., FastAPI dependency yielding a database session., PostgreSQL Business State

### Community 41 - "LinkedIn Plan Orchestration Tests"
Cohesion: 0.33
Nodes (6): asyncio, Sequential queries merge + dedupe + post-filters, enrich budget applied.…, Platform-wide 500-char cap: a >500-char plan query is compacted BEFORE…, test_search_linkedin_people_compacts_overlong_queries(), test_search_linkedin_people_no_queries(), test_search_linkedin_people_plan_orchestration()

### Community 42 - "HTTP Debug Proxy"
Cohesion: 0.47
Nodes (5): auth_ok(), handle_client(), main(), StreamReader, StreamWriter

### Community 43 - "Sliding Window Rate Limiter"
Cohesion: 0.40
Nodes (3): Per-key sliding-window rate limiter (in-memory)., Check if the key is within limit. Returns (allowed, retry_after_s)., SlidingWindowLimiter

## Knowledge Gaps
- **115 isolated node(s):** `entrypoint.sh script`, `BRAVE_CDP_URL`, `career-agent-backend`, `manifest_version`, `name` (+110 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 468 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `CandidateSearchRequest` connect `Sourcing Plan Tests` to `Approval Workflow Schemas`, `Task API Routes`, `Query Plan Soft Caps`?**
  _High betweenness centrality (0.097) - this node is a cross-community bridge._
- **Why does `BrowserError` connect `Browser Runtime Service` to `Candidate Dedup Filtering`, `Task API Routes`, `FastJobs Job Adapter`, `LinkedIn People Search`, `Browser Session Endpoints`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `API Key Authentication` to `Browser Runtime Service`, `Backend Config Settings`, `LLM Reranking and Matching`, `Session Encryption Capture`, `API Security Tests`, `Sources API Tests`, `Database Session Management`, `FastAPI App Startup`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Base` (e.g. with `lifespan()` and `create_all()`) actually correct?**
  _`Base` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `entrypoint.sh script`, `BRAVE_CDP_URL`, `career-agent-backend` to the rest of the system?**
  _115 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Frontend Search UI` be split into smaller, more focused modules?**
  _Cohesion score 0.05192107995846314 - nodes in this community are weakly interconnected._
- **Should `Source Setup API` be split into smaller, more focused modules?**
  _Cohesion score 0.05847781369379959 - nodes in this community are weakly interconnected._