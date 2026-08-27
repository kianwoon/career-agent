/**
 * API client for the Career Agent backend.
 *
 * Calls the FastAPI backend at /api/v1. The backend runs the LangGraph
 * pipeline (search -> extract -> normalize -> deduplicate -> match/rank)
 * and returns ranked results with evidence.
 */

export type SearchMode = "jobs" | "candidates";

export interface SearchRequest {
  query: string;
  mode: SearchMode;
  location?: string;
  /** Source IDs to include; empty/undefined = all enabled sources. */
  sources?: string[];
}

/* --------------------------- Pluggable sources --------------------------- */

export interface SourceView {
  id: string;
  name: string;
  base_url: string;
  domain: string;
  enabled: boolean;
  has_session: boolean;
  flows: Record<string, string>;
  created_at: string;
}

export interface WizardStartResponse {
  wizard_id: string;
  mode: string;
  start_url: string;
}

export interface WizardEvent {
  action: string;
  selector: string;
  text?: string;
  value?: string;
  url?: string;
}

export interface WizardPollResponse {
  events: WizardEvent[];
  total_events: number;
}

export interface WizardCompleteResponse {
  flow_id?: string;
  steps: Record<string, unknown>[];
  card_selectors?: Record<string, unknown> | null;
}

export async function listSources(): Promise<SourceView[]> {
  const base = await resolveApiBase();
  return getJson<SourceView[]>(`${base}/api/v1/sources`);
}

export async function createSource(name: string, baseUrl: string): Promise<SourceView> {
  const base = await resolveApiBase();
  return postJson<SourceView>(`${base}/api/v1/sources`, { name, base_url: baseUrl });
}

export async function deleteSource(id: string): Promise<void> {
  const base = await resolveApiBase();
  const headers: Record<string, string> = {};
  const key = await resolveApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(`${base}/api/v1/sources/${id}`, {
    method: "DELETE",
    headers,
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed with status ${res.status}`);
  }
}

export async function wizardStart(
  sourceId: string,
  mode: "login" | "record",
  flowType?: "find_jobs" | "find_candidates"
): Promise<WizardStartResponse> {
  const base = await resolveApiBase();
  return postJson<WizardStartResponse>(
    `${base}/api/v1/sources/${sourceId}/wizard/start`,
    { mode, flow_type: flowType }
  );
}

export async function wizardStatus(
  sourceId: string,
  mode: string
): Promise<{ url: string; title: string; logged_in: boolean }> {
  const base = await resolveApiBase();
  return getJson(`${base}/api/v1/sources/${sourceId}/wizard/status?mode=${mode}`);
}

export async function wizardCredentials(
  sourceId: string,
  mode: string,
  username: string,
  password: string,
  submit = true
): Promise<{ ok: boolean; reason?: string; submitted?: boolean; url?: string }> {
  const base = await resolveApiBase();
  return postJson(`${base}/api/v1/sources/${sourceId}/wizard/credentials?mode=${mode}`, {
    username,
    password,
    submit,
  });
}

export async function wizardMfa(
  sourceId: string,
  mode: string,
  code: string
): Promise<{ ok: boolean; reason?: string; url?: string }> {
  const base = await resolveApiBase();
  return postJson(`${base}/api/v1/sources/${sourceId}/wizard/mfa?mode=${mode}`, { code });
}

export async function wizardClick(
  sourceId: string,
  mode: string,
  x: number,
  y: number
): Promise<{ ok: boolean }> {
  const base = await resolveApiBase();
  return postJson(`${base}/api/v1/sources/${sourceId}/wizard/click?mode=${mode}`, { x, y });
}

/** URL for the live wizard screenshot (used as <img src>). */
export async function wizardScreenshotUrl(
  sourceId: string,
  mode: string
): Promise<string> {
  const base = await resolveApiBase();
  const key = await resolveApiKey();
  // Include the API key as a query param because <img> cannot set headers.
  return `${base}/api/v1/sources/${sourceId}/wizard/screenshot?mode=${mode}&api_key=${encodeURIComponent(key)}`;
}

export async function wizardComplete(
  sourceId: string,
  mode: string,
  queryHint?: string
): Promise<WizardCompleteResponse> {
  const base = await resolveApiBase();
  return postJson<WizardCompleteResponse>(
    `${base}/api/v1/sources/${sourceId}/wizard/${mode}/complete`,
    { query_hint: queryHint ?? null }
  );
}

export async function wizardCancel(sourceId: string, mode: string): Promise<void> {
  const base = await resolveApiBase();
  await postJson<void>(`${base}/api/v1/sources/${sourceId}/wizard/${mode}/cancel`, {});
}

/** A single piece of evidence backing a match score. */
export interface Evidence {
  field: string;
  value: string;
  source_url?: string | null;
  source_text?: string | null;
}

/** A ranked job or candidate result from the backend. */
export interface SearchResult {
  id: string;
  title: string;
  subtitle?: string | null;
  company?: string | null;
  location?: string | null;
  source: string;
  source_url?: string | null;
  /** Relevance score in the range 0..100 (100 = perfect match). */
  match_score: number;
  match_reason?: string | null;
  evidence: Evidence[];
  gaps: string[];
  recommended_action?: string | null;
  status?: string;
  discovered_at?: string;
  /** Candidate enrichment fields (candidate mode only). */
  summary?: string | null;
  skills?: string[];
  experience?: string | null;
  education?: string | null;
  certifications?: string | null;
  /** Credibility assessment (candidate mode only). */
  credibility?: {
    score: number;
    title_inflation: number;
    tenure_depth: number;
    evidence_ratio: number;
    flags: string[];
  } | null;
}

export interface SearchResponse {
  task_id: string;
  status: "pending" | "running" | "paused" | "waiting_approval" | "completed" | "failed";
  results: SearchResult[];
  summary?: string | null;
  /** Per-source failures, e.g. "MyCareersFuture: Session expired: ...". */
  source_issues?: string[];
}

export interface TaskStatus {
  task_id: string;
  type: SearchMode;
  status: "pending" | "running" | "paused" | "waiting_approval" | "completed" | "failed";
  workflow_state?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
}

// Runtime config: reads backend URL + key from /api/runtime-config (server
// env), so values can be set at deploy time on Koyeb without rebuilding.
import { apiBaseUrl, apiKey } from "./runtime-config";

async function resolveApiBase(): Promise<string> {
  try {
    return await apiBaseUrl();
  } catch {
    return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }
}

async function resolveApiKey(): Promise<string> {
  try {
    return await apiKey();
  } catch {
    return process.env.NEXT_PUBLIC_API_KEY ?? "";
  }
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = await resolveApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed with status ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

async function getJson<T>(url: string): Promise<T> {
  const headers: Record<string, string> = {};
  const key = await resolveApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(url, { headers });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed with status ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

/** Start a search task and return its status. */
export async function startSearch(request: SearchRequest): Promise<TaskStatus> {
  const body =
    request.mode === "jobs"
      ? { query: request.query, location: request.location, sources: request.sources }
      : { query: request.query, location: request.location, sources: request.sources };
  const base = await resolveApiBase();
  return postJson<TaskStatus>(`${base}/api/v1/search/${request.mode}`, body);
}

/** Fetch the ranked results for a completed task. */
export async function fetchTaskResults(taskId: string): Promise<SearchResponse> {
  const base = await resolveApiBase();
  return getJson<SearchResponse>(`${base}/api/v1/tasks/${taskId}/results`);
}

/** Fetch a task's current status. */
export async function fetchTaskStatus(taskId: string): Promise<TaskStatus> {
  const base = await resolveApiBase();
  return getJson<TaskStatus>(`${base}/api/v1/tasks/${taskId}`);
}

/** Approve or reject a pending approval. */
export async function decideApproval(
  approvalId: string,
  decision: "approve" | "reject",
  note?: string,
): Promise<unknown> {
  const base = await resolveApiBase();
  return postJson(`${base}/api/v1/approvals/${approvalId}`, {
    decision,
    note,
  });
}

// ---------------------------------------------------------------------------
// Search history
// ---------------------------------------------------------------------------

export interface SearchHistoryItem {
  task_id: string;
  type: SearchMode;
  query: string;
  status: string;
  result_count: number;
  created_at: string;
  completed_at?: string | null;
}

export interface SearchHistoryResponse {
  items: SearchHistoryItem[];
}

/** Fetch past search history (most recent first). */
export async function fetchSearchHistory(): Promise<SearchHistoryResponse> {
  const base = await resolveApiBase();
  return getJson<SearchHistoryResponse>(`${base}/api/v1/search/history`);
}

// ---------------------------------------------------------------------------
// Browser session management (capture / replay / refresh)
// ---------------------------------------------------------------------------

export interface BrowserSessionView {
  session_id: string;
  status: string;
  url?: string | null;
  title?: string | null;
  needs_human?: boolean;
  reason?: string | null;
}

/** Create a browser session row (persisted in DB). */
export async function createBrowserSession(): Promise<BrowserSessionView> {
  const base = await resolveApiBase();
  return postJson<BrowserSessionView>(`${base}/api/v1/browser/sessions`, {});
}

/** Capture the live signed-in browser's cookies (encrypted, stored). */
export async function captureBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  const base = await resolveApiBase();
  return postJson<BrowserSessionView>(
    `${base}/api/v1/browser/${sessionId}/capture`,
    {},
  );
}

/** Replay a captured session in fresh Chromium and verify login. */
export async function replayBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  const base = await resolveApiBase();
  return postJson<BrowserSessionView>(
    `${base}/api/v1/browser/${sessionId}/replay`,
    {},
  );
}

/** Refresh a near-expiry session by re-capturing from the live browser. */
export async function refreshBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  const base = await resolveApiBase();
  return postJson<BrowserSessionView>(
    `${base}/api/v1/browser/${sessionId}/refresh`,
    {},
  );
}
