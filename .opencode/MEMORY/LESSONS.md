Migrated from global ~/.config/opencode/MEMORY/LESSONS.md on 2026-09-05 — project-local canonical copy.

> **DEPRECATED**: this legacy dump mixed project specifics into global memory. Use per-file `lessons/` for distilled cross-project patterns and project-local `./.opencode/MEMORY/` for everything else. Kept for history only; do NOT append. See MEMORY/README.md routing rule.


## Career bot: debugging the live Koyeb backend (2026-09-02)
- Deployed frontend exposes `/config` (e.g. `https://career-agent-kianwoon-88223cd5.koyeb.app/config`) returning `{apiBaseUrl, apiKey}` — use that key as `X-API-Key` to query the production DB via the API (no DB credentials needed).
- `koyeb` CLI is authenticated; apps: `career-agent` (services career-api + career-web), deploys auto-trigger on push to GitHub main.
- Local backend tests need Postgres on :5432 — `brew services start postgresql@16`, then `DATABASE_URL="postgresql+asyncpg://career:career@localhost:5432/career_agent"` (db+role `career` already exist locally).
- Symptom→cause: "login opens https://jobstreet/" meant the stored source's `base_url` was a bare word — the user pasted the URL into the Name field and the name into the URL field. The extension's `cmdNavigate` prepends `https://` to anything. Fixed by rejecting dotless domains in `create_source` + frontend guard in `handleAddSource`.

## Career bot: "stuck capture session" = missing API key in fetch helper (2026-09-02)
- Symptom: clicking "I'm signed in — capture session" never completed. Backend + extension were fine (curl POST capture → cookies in 2s, PUT session → has_session:true).
- Root cause: `putJson` in frontend/lib/api.ts didn't attach X-API-Key (postJson/getJson did). PUT /agent_session got 401. Same bug in updateSourceEnabled (PATCH). The frontend's `finally` DOES clear wizardBusy, so "stuck" = the error warn + no session saved, flow dead-ends.
- Audit lesson: when an API adds auth middleware, grep ALL fetch helpers (`grep -n "fetch(\|X-API-Key"`) — ad-hoc fetch calls bypass shared helpers.
- Side effect of live testing: JobStreet session was stored on prod via my curl test (has_session was already true from user's own successful capture POST).

## Career bot: diagnosing extension command failures via Koyeb logs (2026-09-02)
- When a new extension command 502s but `/agent/status` is connected: check `koyeb services logs career-agent/career-api`. A `POST /v1/agent/result` right before the failing request = extension RESPONDED with an error (e.g. old extension: "Unknown action: X") — the fix is reloading the extension, not backend code.
- `error code: 502` with `content-type: text/plain` + `server: cloudflare` = Cloudflare edge text, but Koyeb logs show the real origin status (our HTTPException 502 JSON). Don't confuse edge vs origin.
- Extension reload flow: chrome://extensions → Career Agent Browser → ↻ (or remove + load unpacked from /extension). Version bumped to 1.2.0 to make the update visible.
- New in v1.2.0: start_record/stop_record — manual filter-click recorder. Recorded clicks merge into flow steps between search prefix and extract step; replay works via existing cmdRunFlow click support.

## SEEK/JobStreet candidate cards — flow recording gotchas (2026-09-02)
- Symptom: candidate search via custom source returns 1 result titled "Unknown".
- Root causes stacked: (1) card-discovery heuristic picked `div#app` (page root, matches once, no links); (2) backend never mapped extraction fields {title,url} → candidate schema {name,headline,source_url}; (3) dedup key (source, source_url='') collapsed all rows.
- Fix: card selector `[data-testid*='card']` (SEEK talent search cards have data-testid, NO <a> links — name is first line of card text); backend `_normalize_flow_candidate()` in nodes.py maps raw rows + synthesizes source_url; dedup falls back to name.
- Probe trick: drive the user's own extension via `POST /api/v1/agent/execute` (relay, no tunnels) to inspect live DOM with different selectors before touching code.

## Sourcing-plan caps: truncate, don't reject (2026-09-02)
- User rule: external API callers may send >5 boolean queries — must NOT 422.
- Old schemas.py had max_length validators on queries/exclude/platforms → hard 422. Removed; caps enforced in plan_queries()/plan_platforms() ([:5]) and route exclude ([:10]).
- Unknown platform NAMES still 422 loudly (by design — prevents searching the wrong site). Only oversized counts are truncated.
- Tests: test_sourcing_plan.py cap tests rewritten to assert truncation. Live-verified on Koyeb: 8 queries+8 excludes → 201, ran first 5, merged top 10.

## Candidates API: source_platform + openable source_url (2026-09-02)
- SEEK/JobStreet candidate cards have NO hrefs (SPA-only candidate ids, no __NEXT_DATA__) → per-candidate deep links impossible via extraction. source_url falls back to the source's base_url (openable; user searches the name).
- Results are REBUILT FROM DB (MatchEvaluation + Job/Candidate entities) in routes.py GET /tasks/{id}/results — schema changes must be applied at BOTH the matching.py construction AND the DB-rebuild site, or the field stays null live.
- Identity keys: dedup + Candidate persistence now use (source, url, name) because multiple candidates can share one landing URL.

## Koyeb career-agent API route doubling (2026-09-03)
- Koyeb's `/api -> career-api` route rule STRIPS `/api` prefix, so the deployed API answers at `/api/v1/api/v1/...` (the app mounts the router at both `/api/v1` and `/v1`, and Koyeb re-prefixes `/api`). Bare `/api/v1/...` returns 404 with `{"error":{"code":"not_found"}}`; `/v1/...` 307-redirects to the frontend password gate `/login`.
- API key for live checks: GET `/config` (public, frontend runtime config) returns `apiKey`. `/api/v1/agent/status` is the cheap no-auth liveness check.
- Backend test suite: 5 tests (test_graph supervisor, test_security x2, test_sources_api x2) fail locally with `OSError: getaddrinfo` (need live Postgres) — pre-existing on clean main, not regressions.
echo saved
## Playwright rejects Chrome-style sameSite cookie values (career-agent, 2026-09-03)
- `chrome.cookies` returns sameSite as `unspecified|lax|strict|no_restriction`; Playwright `new_context(storage_state=...)` only accepts `Strict|Lax|None`. A `no_restriction` cookie in the stored storage_state crashed the candidate search, which surfaced as a MISLEADING "session expired, re-login" pause message. Real error was in task.error JSON: `Browser.new_context: storageState.cookies[N].sameSite: expected one of (Strict|Lax|None)`.
- Lesson: when a task says "session expired" but the browser is still logged in, fetch the task record first and read `error` — don't trust the human_reason.
- Fix pattern: normalize at BOTH capture time (extension) and load time (backend sanitizer, repairs already-stored blobs without re-login).

## "Session expired" triage ladder (career-agent, 2026-09-03)
Three distinct causes all surfaced as the same pause message:
1. Playwright sameSite crash (fixed in ca11e0c) — error text says "Browser.new_context".
2. Server-side Playwright fallback with stale stored cookie blob — error wording "redirected to a login page" comes from _looks_logged_out (source_flows.py). The extension's run_flow had silently failed first; the server fallback uses the frozen capture, NOT the user's live browser. Fix = re-capture session via extension; extension path failure itself (selector drift) is the thing to check first in extension logs.
3. LinkedIn 0-results + noise relax — error is just needs_human from jobstreet; LinkedIn side showed "0 unique + gate dropped N". Fixed in 3a96652 with _broad_variants quote-free pass.
Rule: always fetch GET /api/v1/api/v1/tasks/{id} and read the `error` JSON before touching sessions.

## LinkedIn "0 results on every query" = soft throttle (2026-09-03)
When a whole plan (strict + relaxed + broad) returns 0 AND fallback-harvest finds 0 /in/ links, the extension tab is being soft-throttled — pages render empty with no login wall. Diagnosis: run a single plain query (e.g. "QC technician") via POST /api/v1/api/v1/search/candidates; if it hits, the pipeline is fine and the plan failed due to tab state/pacing. Fix (0d018a0): extension retries each zero-query once after backoff, aborts after 2 consecutive zeroes with needs_human "throttling". Extension changes require the USER to reload the unpacked extension (chrome://extensions → reload) — backend deploy alone doesn't update background.js.

## Frontend banner-clear check must exempt guest sources (2026-09-03)
First fix required ALL sources has_session to clear the "Re-login required" banner — but FastJobs is guest-browsing and NEVER has_session, so the banner pinned forever (my own regression; I even flagged the risk in the delivery message and then shipped it anyway). Lesson: when the risky edge is named, handle it in the same change, not "if you see it stick". Fixed in 03b2537 by exempting FastJobs.

## SEEK candidate deep links (2026-09-03)
SEEK (sg.employer.seek.com) talent-search candidate cards have NO hrefs; bare base_url is a blank page. Validated deep link = profile name search: /talentsearch/search/profiles?locationList=24553&nation=24553&pageNumber=1&salaryNation=24553&salaryType=MONTHLY&searchId=112aa&searchType=new_search&sortBy=relevance&uncoupledFreeText=<urlencoded name>&willingToRelocate=false. _normalize_flow_candidate builds this when base_url contains seek.com (70bff95).

## Koyeb /api strip broke documented external URLs (fixed 20b7b24)
routes.py's router has INTERNAL prefix="/api/v1". Koyeb strips leading /api, so documented /api/v1/X arrived as /v1/X and 404'd; only the accidental doubled /api/v1/api/v1/X (= /v1/api/v1/X after strip) matched. Fix: middleware in main.py rewrites /v1/<x> (not /v1/api/) → /api/v1/<x>. All three spellings now work live. Lesson: when a router has an internal prefix, proxy path-stripping breaks documented paths SILENTLY — test the documented spelling, not just the one that happens to work.

## LinkedIn extract=0 ≠ throttle: harvest per-page (f58e460, 2026-09-03)
Single complex query task showed "query: 0; fallback-harvest: 2" — extract's container-scoring heuristic zeros out on complex multi-OR result layouts while /in/ links exist on the same page. My throttle-abort (v1.3.0) misread these as throttling and skipped the end-of-plan harvest (blocker set → harvest skipped) so multi-query plans paused. Root pattern: a numeric heuristic returning 0 is ambiguous (empty page vs. unparseable page); always disambiguate with a cheaper direct probe (count raw /in/ anchors) before classifying as throttling. Fix v1.4.0: harvest each page immediately when extract=0; only declare throttle when a page has NO /in/ links.

## LinkedIn: long query + NOT(...) = empty page (4d9e988, 2026-09-03)
THE actual root cause of the external-site "no results" saga: appending NOT (a OR b OR c) to a long (~150+ char) multi-OR base query makes LinkedIn people search serve a truly empty page (zero /in/ links — v1.4.0's per-page harvest proved pages weren't parse-failures). Each part alone works; short base + NOT works. Fix: _apply_excludes skips the clause when len(query)+clause > 140 (MAX_QUERY_NOT_LEN); _filter_excluded post-filter still enforces the full exclude list.
Methodology win that cracked it: reproduce with the EXACT frontend payload (including location/exclude fields), then bisect by removing one field at a time — my earlier probes omitted excludes and "worked", masking the bug for hours. Always replicate the real caller's full payload before believing a repro.

## dict.get(k, default) still returns None when the KEY EXISTS with None (6c332f3)
rerank_candidates crashed with "'NoneType' object is not subscriptable" on flow candidates because _normalize_flow_candidate emits headline=None (key present) and the reranker used cand.get("headline", "")[:200] — the default only applies when the key is MISSING. Fix: (cand.get("headline") or "")[:200]. Rule: when a field can legitimately be None, never rely on .get(k, default) for slicing/formatting; use (get() or default).

## Desktop app = packaged .app, never dev servers (2026-09-04, repeated)
User runs `/Users/kianwoonwong/Downloads/opencode/packages/desktop/dist/mac-arm64/OpenCode.app` (NOT ~/.opencode/bin/opencode, NOT `bun dev`). Before any "give me a build to test" request: run `ps aux | grep -i opencode` FIRST to identify the artifact. Then the only correct chain (desktop AGENTS.md):
1. `cd packages/desktop && OPENCODE_CHANNEL=prod bun run build`
2. `cd packages/desktop && OPENCODE_CHANNEL=prod bun run package:mac`
3. Acceptance gate (required, abort on mismatch): `cd packages/desktop && OPENCODE_CHANNEL=prod APP_DIR=dist/mac-arm64/OpenCode.app bun ./scripts/verify-prod.ts` → must print ✅ embeds channel "prod" and uses opencode.db
Tell the user to quit and reopen the .app; never restart app/server processes myself.

## Candidate search default platforms (b34fb47, 2026-09-03)
POST /search/candidates with no platforms field used to default to ["LinkedIn"] ONLY — the external integrator expected all candidate sources searched and combined. Now defaults to ["LinkedIn", *enabled flow platforms] (currently "jobstreet - candidate"). Both platforms' results merge into one pool and rerank together in agent nodes (for platform in platforms: raw.extend → single score+rerank pass). Explicit platforms[] still opts out.

## Agent-drop mid-plan must not fall through to CDP (9b8592b, 2026-09-03)
Task 0909a681: extension plan ran, then the task paused with "Page.goto: Page crashed" — the CDP/Playwright fallback (stale ngrok BRAVE_CDP_URL → 404 → stored-session browser) executed after an agent dispatch failure mid-plan (websocket blip / MV3 sleep during the 45s throttle-retry wait). Fixes: dispatch failures raise clear BrowserError ("extension agent went offline mid-search"); throttle-retry dispatch failure keeps the throttle pause; CDP fallback entry now logged with the URL. Also: jobstreet flow was latched "broken" by session-expiry flagging and required manual re-record — check flow status in GET /api/v1/sources when a platform leg silently disappears ("no find_candidates flow recorded").

## SEEK flow extraction drift: card=div#app junk row (18d1a0b, 2026-09-04)
Recorded jobstreet flow used card "div#app" + title "a.pswd5d0" — seek rotates obfuscated classes, so extraction returned ONE row (div#app is unique) whose raw_text was the whole page and whose name fell back to the first page line ("Skip to content"). Junk-row guard added in _normalize_flow_candidate (chrome names / titleless mega-text → None, call sites filter). Flow patched live via PATCH /api/v1/sources/{sid}/flows/{fid} to card "article", title "a", status active. Lesson: never record card selectors pointing at app roots, and never title-selectors on hashed classes — they rot silently and the failure looks like "0 results" + one junk row.

## Flow re-record trap + heal (2026-09-04)
agent_record_manual_stop REUSED the existing flow's extract step verbatim, so "Re-record candidates" could never fix a rotten card selector (seek rotates obfuscated classes like a.pswd5d0 — user re-recorded, steps unchanged). Fix 0ac1ae1: backend probes the stored extract live (run_flow + extract, needs >=2 real rows); when dead, extension command find_result_card detects the repeating row container (most siblings with 80+ char multi-line text + a[href]) and the flow is saved with the fresh selector. Lesson: any "re-record" UX that merges old state verbatim can't heal drift — probe before trusting stored selectors.

## Zombie tasks: watchdog + cancel (2026-09-04)
asyncio.create_task(_run_task(...)) with no deadline: a hung server-side call (Playwright fallback) left a task "running" 30+ min, and NO cancel endpoint existed (404 on /cancel). Fix 72d76b4: _run_task_with_watchdog (12-min hard timeout → task failed "Search timed out"), POST /tasks/{id}/cancel (flips to failed "Cancelled by user"), runner refreshes task before persisting and returns early if cancelled. Lesson: any background asyncio.create_task that writes visible state needs BOTH a deadline and a user-visible kill switch from day one.
Also: seek extraction validated working — find_result_card returns "div > div > div > div.pswd5d0._1y3mnyybd" (20 rows), extract with card+fields {title:"a"} → 30 rows/~21 real candidates AFTER wait 6s (3s wait → 0; results load async). Flow PATCHed live with wait 6 + fresh card. agent_record heal didn't trigger during user's re-record (ext version unverified) — the PATCH did the heal manually.

## Combined-run quality issues (2026-09-04, fix 7266087)
First full combined run after flow heal completed (no pause — big win) but: jobstreet leg extracted ONE row = seek placeholder "Your saved searches will appear here" (extraction raced async results render), and gate kept 2 weak LinkedIn rows (substring gate passes generic terms like "biotech"). Fixes: extension cmdRunFlow retries extract once (+5s) when 0 real rows (v1.5.1); backend junk guard blocklists placeholder phrases. Remaining known-weak: LinkedIn-only results score low (3-40) vs jobstreet's enriched rows — external site should expect that when jobstreet leg yields nothing real.

## Pause-policy bugs (2026-09-04, b23a8dd)
Two compounding issues kept pausing combined runs: (1) _search_candidates_via_flow fell back to server Playwright on dispatch failure — for flow sources the stored blob is always stale and seek markup rotates, so the fallback ALWAYS produced "Session expired" pauses; now a soft miss (plan_detail note, no pause). (2) The candidates plan loop let one platform's needs_human pause the run even when other platforms had rows; now pauses only when raw is empty across ALL platforms. Rule: results already obtained must never be discarded because a different source failed.

## Wait for page readiness, never fixed sleeps (2026-09-04, user mandate)
User rule: "we must wait for the page fully loaded before carry next action." Extension previously used blind sleeps (cmdNavigate 2.5s, click 1.5s, press 2s) → raced slow renders (empty-state extraction, fill on missing input). Fix 86c649d (ext v1.6.0): waitForPageReady = document.complete + zero pending resource fetches + DOM innerHTML stable across a 600ms beat; waitForElement polls selector before fill/click/card-extract; click/press use waitForPageReady after action. All new extension actions must use these, never bare sleep-then-act.

## Pre-test full review (2026-09-04, 7511964)
Reviewer subagent audited 4d9e988..86c649d. Fixed: (HIGH) dispatch cancellation left zombie commands in pending that poll() would re-hand to the extension (BaseException cleanup + age-only stale filter); (HIGH) stop_record heal could run find_result_card on a LOGIN page (guest job cards false-positive) and bumped last_verified_at without verification — now skips heal on needs_human probe, returns SourceFlowView.note, only verifies on real extraction; (MED) blocked-platform reason preserved in plan_detail; (MED) final cancel-race check before results commit. Deferred LOWs: substring junk markers may drop "NextGen" titles; find_result_card login-page guard not added client-side; probe conflates no-agent with rotten selector.
ALSO: discovered user had re-recorded and flow extract step became action:extract_cards with [data-testid=profile-card] — a selector that does NOT exist on seek (0 rows). Re-patched flow to verified card "div > div > div > div.pswd5d0._1y3mnyybd" + fields {title:"a"} (probe-validated 20-30 rows). lesson: ALWAYS probe a flow's extract step live before trusting it; data-testid guesses on seek are wrong.

## FATAL RULE (user mandate, 2026-09-04): NO WAIT >120s
User will not tolerate any single wait longer than 120 seconds. NEVER use sleep/poll loops waiting on backend tasks. Pattern: check once → report instantly → let the user prompt the next check. Backend long-runners are bounded by their own 12-min watchdog; user-facing turn time must stay under 2 minutes. Violating this is treated as a fatal compliance failure.

## Fast-fail on agent reload (2026-09-04, 354ebaf, ext v1.7.0)
12-min jobstreet hang: worker reload mid-command orphaned the executing command; caller held dispatch lock for full 420s; queued callers stacked serially. Fixes: dispatch lock_wait_s=60 (bounded); /agent/poll?boot=<id> — backend note_boot() fails all pending commands instantly when boot id changes; flow timeout 420→240 (obsolete since v1.6.1 busy-polling). Deployment: verify by GET /api/v1/agent/status after ~2-3 min (Koyeb build).

## Recorder blank-tab bug (2026-09-04, 6ac78c4, ext v1.7.1)
"Record candidates" captured zero clicks after extension reload: worker restart resets agentTabId → start_record's execOnTab created a hidden about:blank tab and installed the recorder THERE; user clicked filters on their real seek tab → stop_record 422 "No clicks were recorded". Fix: start_record takes source base_url, reuses agent tab / attaches to user's existing tab on that site / opens a NEW ACTIVE tab. Lesson: any extension feature that depends on agentTabId breaks silently after worker reload — resolve the target tab from the task context (source URL), never create blind blank tabs.

## Record button path (2026-09-04, f7fdf00)
User's "Record candidates" button calls POST /agent_record (one-shot agent discover_flow), NOT /agent_record/start+/stop (manual click recorder). discover_flow = legacy guest-page heuristic (first visible text input + card scorer) that cannot drive seek's React search → throws "Could not find repeated result cards" → 502. Fix: on seek.com domains, _agent_discover runs the proven steps + find_result_card (detects card live); legacy path kept for other sites. Lesson: before debugging an endpoint, confirm WHICH endpoint the UI actually calls — I spent a cycle fixing the wrong one (start/stop blank-tab bug was real but not what the button hits). The double /api/api/v1 in frontend URLs is correct by design: Koyeb strips the first /api (route rule), backend router owns /api/v1.

## Record button 502 root cause (2026-09-04, 5bed95d)
UVICORN LOG disproved the deploy-collision theory: 502 came from OUR app right after POST /agent/result (extension posted empty run_flow result). Root cause: cmdRunFlow's step loop handles only navigate/fill/click/press/wait/card — a find_result_card STEP is silently skipped → {results:[]} → no card → HTTPException 502. Fix: dispatch run_flow (search steps) then find_result_card as its OWN top-level command. Lesson: (1) steps-in-run_flow and top-level commands are different dispatch surfaces — check the step loop before embedding special steps; (2) when a 502 has no JSON body, read the uvicorn access log FIRST to see who emitted it; (3) Koyeb's /api strip explains frontend /api/api/v1 URLs (first /api for Koyeb, /api/v1 is the backend router prefix — by design).

## asyncio.Lock is not reentrant — busy-retry recursion deadlocks (2026-09-04)
AgentRegistry.dispatch's "agent busy" retry recursed into dispatch() while still holding _exec_lock → retry waited 60s for its OWN lock → "Agent busy — dispatch lock not released within 60s". Fix afea258: release lock before retry, `locked` flag guards finally so no double-release. Rule: before any retry-by-recursion inside `async with lock` / acquire-release pair, release first or restructure into a loop. Smoke test pattern: fake resolver rejects first command, succeeds second; assert lock.locked() is False after.

## poll() must deliver each command once — duplicate loops cause busy-meltdowns (2026-09-04)
Extension MV3 can run MULTIPLE concurrent poll loops (alarm + startup + install kickers, multiplied by reloads). Backend poll() handed the same command to every loop; busy loop rejected its own duplicate as "agent busy", resolve() raised RuntimeError, retry dispatched another command next loop also grabbed... meltdown: timeout 120s→5s→504. Fix 721d385: Command.claimed flag, poll() delivers once; "agent busy" fails fast ("wait ~10s, press Record again") instead of retrying. Rule: any poll-based command queue with a single-executor client must claim-on-delivery — retries on top of duplicates multiply the problem.
