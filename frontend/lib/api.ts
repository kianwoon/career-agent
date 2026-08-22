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

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Optional API key for the frontend to authenticate against the backend.
// Configure via NEXT_PUBLIC_API_KEY in .env.local. If empty, requests go
// unauthenticated (only works when the backend has API_KEYS empty = dev).
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

export function searchEndpoint(mode: SearchMode): string {
  return `${API_BASE_URL}/api/v1/search/${mode}`;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
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
  if (API_KEY) headers["X-API-Key"] = API_KEY;
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
      ? { query: request.query, location: request.location }
      : { query: request.query, location: request.location };
  return postJson<TaskStatus>(searchEndpoint(request.mode), body);
}

/** Fetch the ranked results for a completed task. */
export async function fetchTaskResults(taskId: string): Promise<SearchResponse> {
  return getJson<SearchResponse>(`${API_BASE_URL}/api/v1/tasks/${taskId}/results`);
}

/** Fetch a task's current status. */
export async function fetchTaskStatus(taskId: string): Promise<TaskStatus> {
  return getJson<TaskStatus>(`${API_BASE_URL}/api/v1/tasks/${taskId}`);
}

/** Approve or reject a pending approval. */
export async function decideApproval(
  approvalId: string,
  decision: "approve" | "reject",
  note?: string,
): Promise<unknown> {
  return postJson(`${API_BASE_URL}/api/v1/approvals/${approvalId}`, {
    decision,
    note,
  });
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
  return postJson<BrowserSessionView>(
    `${API_BASE_URL}/api/v1/browser/sessions`,
    {},
  );
}

/** Capture the live signed-in browser's cookies (encrypted, stored). */
export async function captureBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  return postJson<BrowserSessionView>(
    `${API_BASE_URL}/api/v1/browser/${sessionId}/capture`,
    {},
  );
}

/** Replay a captured session in fresh Chromium and verify login. */
export async function replayBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  return postJson<BrowserSessionView>(
    `${API_BASE_URL}/api/v1/browser/${sessionId}/replay`,
    {},
  );
}

/** Refresh a near-expiry session by re-capturing from the live browser. */
export async function refreshBrowserSession(
  sessionId: string,
): Promise<BrowserSessionView> {
  return postJson<BrowserSessionView>(
    `${API_BASE_URL}/api/v1/browser/${sessionId}/refresh`,
    {},
  );
}
