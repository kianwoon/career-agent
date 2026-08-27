"use client";

import { useEffect, useState } from "react";
import {
  startSearch,
  fetchTaskResults,
  fetchSearchHistory,
  createBrowserSession,
  captureBrowserSession,
  replayBrowserSession,
  refreshBrowserSession,
  listSources,
  createSource,
  deleteSource,
  wizardStart,
  wizardPoll,
  wizardComplete,
  wizardCancel,
  type SearchMode,
  type SearchResult,
  type Evidence,
  type BrowserSessionView,
  type SearchHistoryItem,
  type SourceView,
} from "@/lib/api";

type Phase = "idle" | "running" | "completed" | "error";
type Verdict = "approved" | "rejected";

interface TimelineEvent {
  id: string;
  kind: "info" | "success" | "action" | "warn";
  text: string;
  time: string;
}

/** Display metadata for every job-source connector the backend can return. */
const SOURCE_META: Record<string, { label: string; short: string; color: string }> = {
  linkedin: {
    label: "LinkedIn",
    short: "LI",
    color: "#0a66c2",
  },
  mycareersfuture: {
    label: "MyCareersFuture",
    short: "MCF",
    color: "#5e2ca5",
  },
  fastjobs: {
    label: "FastJobs",
    short: "FJ",
    color: "#e87722",
  },
  linkedin_people: {
    label: "LinkedIn People",
    short: "LIP",
    color: "#0a66c2",
  },
  seed: {
    label: "Sample",
    short: "SEED",
    color: "#6b7280",
  },
};

function sourceMeta(source: string) {
  return SOURCE_META[source] ?? {
    label: source || "Unknown",
    short: (source || "?").slice(0, 3).toUpperCase(),
    color: "#6b7280",
  };
}

const PLACEHOLDER_QUERY = "senior frontend engineer, remote, TypeScript";
const PLACEHOLDER_CANDIDATE_QUERY = "Java, Kafka, payments, microservices, banking";

let eventSeq = 0;

function nextEventId(): string {
  eventSeq += 1;
  return `evt-${Date.now()}-${eventSeq}`;
}

function nowTime(): string {
  return new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function addEvent(
  list: TimelineEvent[],
  kind: TimelineEvent["kind"],
  text: string
): TimelineEvent[] {
  return [{ id: nextEventId(), kind, text, time: nowTime() }, ...list];
}

export default function Home() {
  const [mode, setMode] = useState<SearchMode>("jobs");
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  // Which sources are shown. Empty set = show all. Populated after a search.
  const [activeSources, setActiveSources] = useState<Set<string>>(new Set());
  // Pluggable sources (user-registered sites) + selected subset for searches.
  const [customSources, setCustomSources] = useState<SourceView[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set());
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [wizardBusy, setWizardBusy] = useState<string | null>(null);
  const [wizardHint, setWizardHint] = useState<string | null>(null);
  const [takeoverActive, setTakeoverActive] = useState(false);
  const [browserSession, setBrowserSession] = useState<BrowserSessionView | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [history, setHistory] = useState<SearchHistoryItem[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([
    {
      id: nextEventId(),
      kind: "info",
      text: "Agent ready — waiting for a search request.",
      time: nowTime(),
    },
  ]);

  const isRunning = phase === "running";
  const judgedCount = Object.keys(verdicts).length;

  /** Reset the source filter to show all sources present in a result set. */
  function syncSourceFilter(newResults: SearchResult[]) {
    setActiveSources(new Set(newResults.map((r) => r.source)));
  }

  /** Toggle a source in the filter (empty set means "show all"). */
  function toggleSource(source: string) {
    setActiveSources((prev) => {
      const next = new Set(prev);
      if (next.has(source)) {
        next.delete(source);
      } else {
        next.add(source);
      }
      return next;
    });
  }

  const shownResults = results.filter(
    (r) => activeSources.size === 0 || activeSources.has(r.source)
  );

  // Load past searches on mount.
  useEffect(() => {
    loadHistory();
    listSources()
      .then((s) => setCustomSources(s))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function reloadSources() {
    try {
      setCustomSources(await listSources());
    } catch {
      /* ignore */
    }
  }

  function toggleSelectedSource(id: string) {
    setSelectedSourceIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleAddSource() {
    if (!newSourceName.trim() || !newSourceUrl.trim()) return;
    setWizardBusy("create");
    try {
      const src = await createSource(newSourceName.trim(), newSourceUrl.trim());
      setTimeline((prev) => addEvent(prev, "success", `Source added: ${src.name} (${src.domain}). Now sign in via the wizard.`));
      setNewSourceName("");
      setNewSourceUrl("");
      await reloadSources();
    } catch (e) {
      setTimeline((prev) => addEvent(prev, "warn", `Add source failed: ${e instanceof Error ? e.message : e}`));
    } finally {
      setWizardBusy(null);
    }
  }

  async function handleWizard(source: SourceView, mode: "login" | "record", flowType?: "find_jobs" | "find_candidates") {
    const key = `${source.id}:${mode}:${flowType ?? ""}`;
    setWizardBusy(key);
    try {
      await wizardStart(source.id, mode, flowType);
      const stepLabel =
        mode === "login"
          ? "Sign in to the site in the browser window that just opened."
          : flowType === "find_jobs"
            ? "In the browser window: search for a job like a normal user (e.g. type a role and press search)."
            : "In the browser window: search for a candidate like a normal user.";
      setTimeline((prev) =>
        addEvent(prev, "action", `Setup started for ${source.name}. ${stepLabel} When finished, press "Done" here.`)
      );
      setWizardHint(
        mode === "login"
          ? `A browser tab opened on ${source.domain}. Sign in there, then come back and press Done.`
          : `${"1) Do ONE search like a normal user.  2) Alt-click a result card to mark it (required).  3) Press Done."}`
      );

      // Poll events while the user drives the wizard browser.
      const poll = setInterval(() => {
        wizardPoll(source.id, mode).catch(() => clearInterval(poll));
      }, 1500);
      (window as unknown as { __wizPoll?: ReturnType<typeof setInterval> }).__wizPoll = poll;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setTimeline((prev) =>
        addEvent(
          prev,
          "warn",
          msg.includes("CDP")
            ? "Could not reach your browser. Start the browser bridge on your computer (run ./tunnel.sh start), then try again."
            : `Wizard start failed: ${msg}`
        )
      );
      setWizardHint(null);
      setWizardBusy(null);
    }
  }

  async function handleWizardDone(source: SourceView, mode: "login" | "record", flowType?: "find_jobs" | "find_candidates", queryHint?: string) {
    const poll = (window as unknown as { __wizPoll?: ReturnType<typeof setInterval> }).__wizPoll;
    if (poll) clearInterval(poll);
    setWizardBusy(`${source.id}:${mode}:${flowType ?? ""}`);
    try {
      const res = await wizardComplete(source.id, mode, queryHint);
      if (mode === "record" && res.flow_id) {
        setTimeline((prev) => addEvent(prev, "success", `Flow saved for ${source.name}: ${res.steps.length} steps.`));
      } else if (mode === "login") {
        setTimeline((prev) => addEvent(prev, "success", `Session saved for ${source.name}.`));
      }
      await reloadSources();
    } catch (e) {
      setTimeline((prev) => addEvent(prev, "warn", `Wizard complete failed: ${e instanceof Error ? e.message : e}`));
    } finally {
      setWizardBusy(null);
      setWizardHint(null);
    }
  }

  async function handleWizardCancel(source: SourceView, mode: string) {
    const poll = (window as unknown as { __wizPoll?: ReturnType<typeof setInterval> }).__wizPoll;
    if (poll) clearInterval(poll);
    await wizardCancel(source.id, mode).catch(() => {});
    setWizardBusy(null);
    setWizardHint(null);
    setTimeline((prev) => addEvent(prev, "info", "Wizard cancelled."));
  }

  async function handleDeleteSource(source: SourceView) {
    await deleteSource(source.id).catch(() => {});
    setSelectedSourceIds((prev) => {
      const next = new Set(prev);
      next.delete(source.id);
      return next;
    });
    await reloadSources();
    setTimeline((prev) => addEvent(prev, "info", `Source removed: ${source.name}.`));
  }

  function handleModeChange(next: SearchMode) {
    if (isRunning) return;
    setMode(next);
    setTimeline((prev) =>
      addEvent(
        prev,
        "action",
        `Mode switched to ${next === "jobs" ? "Job Search" : "Candidate Search"}.`
      )
    );
  }

  async function handleRunSearch() {
    if (!query.trim() || isRunning) return;
    setPhase("running");
    setResults([]);
    setVerdicts({});
    setTaskId(null);
    setTimeline((prev) =>
      addEvent(
        prev,
        "action",
        `Search started (${mode === "jobs" ? "jobs" : "candidates"}): "${query.trim()}".`
      )
    );

    try {
      // Start the task on the backend.
      const task = await startSearch({
        query: query.trim(),
        mode,
        location: mode === "jobs" ? "Singapore" : undefined,
        sources: selectedSourceIds.size ? Array.from(selectedSourceIds) : undefined,
      });
      setTaskId(task.task_id);
      setTimeline((prev) =>
        addEvent(prev, "info", `Task created: ${task.task_id} (${task.status}).`)
      );

      // Poll for results. Searches can take 1.5-3 min (LinkedIn navigation +
      // profile extraction + LLM rerank), so poll up to ~4 minutes.
      let response;
      for (let attempt = 0; attempt < 100; attempt++) {
        await new Promise((r) => setTimeout(r, 2500));
        response = await fetchTaskResults(task.task_id);
        if (response.status === "completed") break;
        if (response.status === "failed") break;
        if (response.status === "paused") break;
      }

      if (!response) {
        throw new Error("Timed out waiting for the task to complete.");
      }
      if (response.status === "failed") {
        throw new Error("Backend task failed.");
      }
      if (response.status === "paused") {
        setPhase("error");
        setTimeline((prev) =>
          addEvent(
            prev,
            "warn",
            "Search paused — the browser session needs human attention (MFA/CAPTCHA/expired login)."
          )
        );
        return;
      }

      setPhase("completed");
      setResults(response.results ?? []);
      syncSourceFilter(response.results ?? []);
      setTimeline((prev) =>
        addEvent(
          prev,
          "success",
          `Search complete — ${(response.results ?? []).length} results returned.`
        )
      );
    } catch (err) {
      setPhase("error");
      setTimeline((prev) =>
        addEvent(
          prev,
          "warn",
          `Search failed: ${err instanceof Error ? err.message : "unknown error"}`
        )
      );
    }
    // Refresh history after any search attempt.
    loadHistory();
  }

  async function loadHistory() {
    try {
      const resp = await fetchSearchHistory();
      setHistory(resp.items ?? []);
    } catch {
      // Non-fatal — history is a convenience, not core.
    } finally {
      setHistoryLoaded(true);
    }
  }

  async function handleViewHistory(item: SearchHistoryItem) {
    if (sessionBusy) return;
    setSessionBusy(true);
    try {
      const response = await fetchTaskResults(item.task_id);
      if (response.status === "completed") {
        setPhase("completed");
        setResults(response.results ?? []);
        syncSourceFilter(response.results ?? []);
        setTaskId(item.task_id);
        setMode(item.type);
        setQuery(item.query);
        setVerdicts({});
        setTimeline((prev) =>
          addEvent(
            prev,
            "info",
            `Loaded past ${item.type === "jobs" ? "job" : "candidate"} search: "${item.query}" (${response.results?.length ?? 0} results).`
          )
        );
      } else {
        setTimeline((prev) =>
          addEvent(prev, "warn", `Past search ${item.task_id} is ${item.status} — no results to show.`)
        );
      }
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Failed to load past search: ${e instanceof Error ? e.message : "error"}`)
      );
    } finally {
      setSessionBusy(false);
    }
  }

  function handleTakeover() {
    setTakeoverActive((active) => {
      setTimeline((prev) =>
        addEvent(
          prev,
          active ? "action" : "warn",
          active
            ? "Returned control to the agent."
            : "Human takeover — manual control engaged."
        )
      );
      return !active;
    });
  }

  // Lazily create a browser session row on first use.
  async function ensureSession(): Promise<BrowserSessionView> {
    if (browserSession) return browserSession;
    const s = await createBrowserSession();
    setBrowserSession(s);
    return s;
  }

  async function handleCapture() {
    if (sessionBusy) return;
    setSessionBusy(true);
    try {
      const s = await ensureSession();
      const res = await captureBrowserSession(s.session_id);
      setBrowserSession(res);
      setTimeline((prev) =>
        addEvent(
          prev,
          res.status === "captured" ? "success" : "warn",
          res.status === "captured"
            ? "Captured signed-in session (cookies encrypted & stored)."
            : `Capture needs human: ${res.reason ?? "unknown"}`
        )
      );
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Capture failed: ${e instanceof Error ? e.message : "error"}`)
      );
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleReplay() {
    if (sessionBusy) return;
    setSessionBusy(true);
    try {
      const s = await ensureSession();
      const res = await replayBrowserSession(s.session_id);
      setBrowserSession(res);
      setTimeline((prev) =>
        addEvent(
          prev,
          res.status === "ready" ? "success" : "warn",
          res.status === "ready"
            ? "Replayed session — logged in via fresh Chromium."
            : `Replay needs human: ${res.reason ?? "session expired"}`
        )
      );
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Replay failed: ${e instanceof Error ? e.message : "error"}`)
      );
    } finally {
      setSessionBusy(false);
    }
  }

  async function handleRefresh() {
    if (sessionBusy) return;
    setSessionBusy(true);
    try {
      const s = await ensureSession();
      const res = await refreshBrowserSession(s.session_id);
      setBrowserSession(res);
      setTimeline((prev) =>
        addEvent(
          prev,
          res.status === "captured" ? "success" : "warn",
          res.status === "captured"
            ? "Refreshed session cookies."
            : `Refresh needs human: ${res.reason ?? "browser not running"}`
        )
      );
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Refresh failed: ${e instanceof Error ? e.message : "error"}`)
      );
    } finally {
      setSessionBusy(false);
    }
  }

  function handleVerdict(id: string, verdict: Verdict) {
    if (phase !== "completed") return;
    setVerdicts((prev) => ({ ...prev, [id]: verdict }));
    setTimeline((prev) =>
      addEvent(
        prev,
        verdict === "approved" ? "success" : "warn",
        `${verdict === "approved" ? "Approved" : "Rejected"} result ${id}.`
      )
    );
  }

  const statusLabel = (() => {
    switch (phase) {
      case "running":
        return "Agent is searching…";
      case "completed":
        return "Search completed";
      case "error":
        return "Search failed";
      default:
        return "Idle";
    }
  })();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-dot" aria-hidden="true" />
          <div>
            <h1>Career Agent</h1>
            <p>Job &amp; candidate search with human-in-the-loop review</p>
          </div>
        </div>

        <div className="mode-toggle" role="tablist" aria-label="Search mode">
          <button
            className={mode === "jobs" ? "active" : ""}
            onClick={() => handleModeChange("jobs")}
            disabled={isRunning}
            aria-pressed={mode === "jobs"}
          >
            Job Search
          </button>
          <button
            className={mode === "candidates" ? "active" : ""}
            onClick={() => handleModeChange("candidates")}
            disabled={isRunning}
            aria-pressed={mode === "candidates"}
          >
            Candidate Search
          </button>
        </div>
      </header>

      {takeoverActive && (
        <div className="banner takeover-banner" role="status">
          <strong>Human takeover active.</strong> The agent is paused; you are
          in manual control. Review results below and approve or reject before
          returning control.
        </div>
      )}

      <main className="layout">
        <section className="panel search-panel">
          <div className="search-row">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRunSearch();
              }}
              placeholder={
                mode === "jobs" ? PLACEHOLDER_QUERY : PLACEHOLDER_CANDIDATE_QUERY
              }
              disabled={isRunning}
              aria-label="Search query"
            />
            <button
              className="btn primary"
              onClick={handleRunSearch}
              disabled={isRunning || !query.trim()}
            >
              {isRunning ? "Searching…" : "Run Search"}
            </button>
            <button
              className={`btn takeover${takeoverActive ? " active" : ""}`}
              onClick={handleTakeover}
              disabled={isRunning}
              aria-pressed={takeoverActive}
            >
              {takeoverActive ? "Return to Agent" : "Take Over"}
            </button>
          </div>

          {/* ---------------- Sources panel ---------------- */}
          <div className="sources-panel">
            <div className="sources-row">
              <span className="sources-label">Sources:</span>
              {customSources.length === 0 && (
                <span className="sources-empty">built-in only (LinkedIn, MyCareersFuture, FastJobs)</span>
              )}
              {customSources.map((s) => {
                const ready = s.has_session && (mode === "jobs" ? s.flows.find_jobs === "active" : s.flows.find_candidates === "active");
                return (
                  <label key={s.id} className={`source-chip ${ready ? "ready" : ""}`} title={`${s.domain} — ${ready ? "ready" : "setup incomplete"}`}>
                    <input
                      type="checkbox"
                      checked={selectedSourceIds.has(s.id)}
                      onChange={() => toggleSelectedSource(s.id)}
                      disabled={isRunning || !ready}
                    />
                    {s.name}
                    {!ready && <span className="source-warn"> (setup needed)</span>}
                    <button
                      className="source-remove"
                      onClick={(e) => { e.preventDefault(); handleDeleteSource(s); }}
                      aria-label={`Remove ${s.name}`}
                    >
                      ×
                    </button>
                  </label>
                );
              })}
              {selectedSourceIds.size === 0 && customSources.some((s) => s.has_session) && (
                <span className="sources-empty">(all enabled sources included)</span>
              )}
            </div>

            <details className="add-source">
              <summary>Add a new source</summary>
              <div className="add-source-body">
                <input
                  type="text"
                  value={newSourceName}
                  onChange={(e) => setNewSourceName(e.target.value)}
                  placeholder="Name, e.g. FastJob"
                  aria-label="Source name"
                />
                <input
                  type="text"
                  value={newSourceUrl}
                  onChange={(e) => setNewSourceUrl(e.target.value)}
                  placeholder="URL, e.g. fastjob.com"
                  aria-label="Source URL"
                />
                <button className="btn" onClick={handleAddSource} disabled={wizardBusy === "create" || !newSourceName.trim() || !newSourceUrl.trim()}>
                  Add
                </button>
              </div>
              {customSources
                .filter((s) => !s.has_session || !s.flows.find_jobs || !s.flows.find_candidates)
                .map((s) => (
                  <div key={s.id} className="wizard-steps">
                    <strong>{s.name}</strong> setup:
                    <div className="wizard-actions">
                      <button className="btn small" disabled={!!wizardBusy} onClick={() => handleWizard(s, "login")}>
                        {s.has_session ? "Re-login" : "1. Login"}
                      </button>
                      {s.has_session && (
                        <>
                          <button className="btn small" disabled={!!wizardBusy} onClick={() => handleWizard(s, "record", "find_jobs")}>
                            {s.flows.find_jobs === "active" ? "Re-record" : "2. Record"} jobs flow
                          </button>
                          <button className="btn small" disabled={!!wizardBusy} onClick={() => handleWizard(s, "record", "find_candidates")}>
                            {s.flows.find_candidates === "active" ? "Re-record" : "3. Record"} candidates flow
                          </button>
                        </>
                      )}
                      <button
                        className="btn small"
                        disabled={!!wizardBusy || wizardBusy !== `${s.id}:record:${mode === "jobs" ? "find_jobs" : "find_candidates"}`}
                        onClick={() => handleWizardDone(s, "record", mode === "jobs" ? "find_jobs" : "find_candidates", query)}
                      >
                        Done
                      </button>
                      <button
                        className="btn small"
                        disabled={!wizardBusy?.startsWith(`${s.id}:`)}
                        onClick={() => handleWizardCancel(s, "record")}
                      >
                        Cancel
                      </button>
                    </div>
                    <span className="wizard-hint">
                      {wizardHint ??
                        "Use the buttons above to set up this source, one step at a time."}
                    </span>
                  </div>
                ))}
            </details>
          </div>

          <div className="task-status" role="status">
            <div className="task-status-row">
              <span
                className={`status-dot ${
                  phase === "running"
                    ? "running"
                    : phase === "completed"
                      ? "completed"
                      : phase === "error"
                        ? "error"
                        : ""
                }`}
                aria-hidden="true"
              />
              <span className="status-label">{statusLabel}</span>
              {isRunning && <span className="spinner" aria-hidden="true" />}
            </div>
            <div className="task-meta">
              {taskId ? (
                <>
                  <span className="mono">{taskId}</span>
                  <span className="ok">✓ done</span>
                </>
              ) : (
                <span className="mono">no active task</span>
              )}
            </div>
          </div>
        </section>

        {wizardBusy === null && (
          <section className="panel history-panel">
          <div className="panel-head">
            <h2>Past Searches</h2>
            <button className="link-btn" onClick={loadHistory} disabled={sessionBusy}>
              ↻ Refresh
            </button>
          </div>
          {!historyLoaded ? (
            <div className="empty small">Loading history…</div>
          ) : history.length === 0 ? (
            <div className="empty small">No past searches yet.</div>
          ) : (
            <ul className="history-list">
              {history.map((item) => (
                <li key={item.task_id}>
                  <button
                    className="history-item"
                    onClick={() => handleViewHistory(item)}
                    disabled={sessionBusy}
                    title="Click to view past results"
                  >
                    <span className={`history-type ${item.type}`}>
                      {item.type === "jobs" ? "Job" : "Cand"}
                    </span>
                    <span className="history-query">{item.query}</span>
                    <span className="history-meta">
                      <span className={`history-status ${item.status}`}>{item.status}</span>
                      <span className="history-count">{item.result_count}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        )}

        <div className="columns">
          <section className="panel results-panel">
            <div className="panel-head">
              <h2>
                Ranked{" "}
                {mode === "jobs" ? "Jobs" : "Candidates"}
              </h2>
              <span className="count">
                {results.length > 0
                  ? `${judgedCount}/${results.length} judged`
                  : "no results"}
              </span>
            </div>

            {results.length > 0 && (
              <div className="source-distribution">
                {Object.entries(
                  results.reduce<Record<string, number>>((acc, r) => {
                    acc[r.source] = (acc[r.source] || 0) + 1;
                    return acc;
                  }, {})
                )
                  .sort(([, a], [, b]) => b - a)
                  .map(([source, count]) => {
                    const meta = sourceMeta(source);
                    const active = activeSources.size === 0 || activeSources.has(source);
                    return (
                      <button
                        key={source}
                        className={`source-chip${active ? "" : " muted"}`}
                        onClick={() => toggleSource(source)}
                        title={`${active ? "Hide" : "Show"} ${meta.label} results`}
                      >
                        <span
                          className="source-dot"
                          style={{ backgroundColor: meta.color }}
                        />
                        {meta.label}
                        <span className="source-count">{count}</span>
                      </button>
                    );
                  })}
              </div>
            )}

            {results.length === 0 ? (
              <div className="empty">
                {phase === "running"
                  ? "Agent is browsing the web and collecting results…"
                  : phase === "error"
                    ? "Search failed — check the agent logs and try again."
                    : "Run a search to see ranked results here."}
              </div>
            ) : shownResults.length === 0 ? (
              <div className="empty">
                No results match the selected sources — try toggling the filter chips above.
              </div>
            ) : (
              <div className="results-list">
                {shownResults.map((r) => (
                  <ResultCard
                    key={r.id}
                    result={r}
                    verdict={verdicts[r.id]}
                    onApprove={() => handleVerdict(r.id, "approved")}
                    onReject={() => handleVerdict(r.id, "rejected")}
                  />
                ))}
              </div>
            )}
          </section>

          <aside className="side">
            <section className="panel">
              <div className="panel-head">
                <h2>Browser Session</h2>
                {isRunning && (
                  <span className="live-dot">Live</span>
                )}
              </div>
              <div className="browser-window">
                <div className="browser-bar">
                  <div className="browser-dots">
                    <i />
                    <i />
                    <i />
                  </div>
                  <span className="browser-url mono">
                    {isRunning
                      ? "agent-browser — searching…"
                      : "https://www.linkedin.com/jobs/"}
                  </span>
                </div>
                <div className="browser-body">
                  <p className="browser-placeholder">
                    {isRunning
                      ? "Live browser preview will appear here in a later phase."
                      : "Browser session idle. A live preview of the agent's browser will stream here once the session is connected."}
                  </p>
                </div>
              </div>
              <dl className="session-meta">
                <div>
                  <dt>Session</dt>
                  <dd className="mono">{takeoverActive ? "manual" : "agent-run"}</dd>
                </div>
                <div>
                  <dt>Mode</dt>
                  <dd>{mode === "jobs" ? "Job Search" : "Candidate Search"}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>
                    {sessionBusy
                      ? "busy…"
                      : browserSession?.status === "captured"
                        ? "captured"
                        : browserSession?.status === "ready"
                          ? "ready"
                          : isRunning
                            ? "active"
                            : "idle"}
                  </dd>
                </div>
                <div>
                  <dt>Takeover</dt>
                  <dd>{takeoverActive ? "engaged" : "available"}</dd>
                </div>
              </dl>
              <div className="session-actions">
                <button
                  className="btn session-btn"
                  onClick={handleCapture}
                  disabled={sessionBusy}
                >
                  Capture
                </button>
                <button
                  className="btn session-btn"
                  onClick={handleReplay}
                  disabled={sessionBusy}
                >
                  Replay
                </button>
                <button
                  className="btn session-btn"
                  onClick={handleRefresh}
                  disabled={sessionBusy}
                >
                  Refresh
                </button>
              </div>
            </section>

            <section className="panel">
              <div className="panel-head">
                <h2>Agent Activity</h2>
                <span className="count">{timeline.length}</span>
              </div>
              <ul className="timeline-list" aria-label="Agent activity timeline">
                {timeline.map((e) => (
                  <li key={e.id} className={`event ${e.kind}`}>
                    <span className="event-dot" aria-hidden="true" />
                    <div>
                      <span className="event-text">{e.text}</span>
                      <span className="event-time mono">{e.time}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </aside>
        </div>
      </main>
    </div>
  );
}

/* ------------------------- Result card ---------------------------- */

function ResultCard({
  result,
  verdict,
  onApprove,
  onReject,
}: {
  result: SearchResult;
  verdict: Verdict | undefined;
  onApprove: () => void;
  onReject: () => void;
}) {
  const scoreClass =
    result.match_score >= 70 ? "high" : result.match_score >= 50 ? "mid" : "low";
  const subtitle =
    result.subtitle ?? result.company ?? "Unknown";
  const location = result.location ?? "";
  const source = sourceMeta(result.source);

  return (
    <article
      className={`result-card${verdict ? ` ${verdict}` : ""}`}
      aria-label={`${result.title} — ${subtitle}`}
    >
      <div className="result-top">
        <div className="result-title">
          <h3>{result.title}</h3>
          <span className="company">
            {subtitle}
            {location ? ` · ${location}` : ""}
          </span>
          <span className="result-source-row">
            <span
              className="source-badge"
              style={{ backgroundColor: `${source.color}1a`, borderColor: source.color }}
              title={`Source: ${source.label}`}
            >
              <span className="source-dot" style={{ backgroundColor: source.color }} />
              {source.label}
            </span>
          </span>
        </div>
        <span className={`score-badge ${scoreClass}`} title="Match score (0-100)">
          {result.match_score.toFixed(0)}
        </span>
      </div>
      {result.match_reason && <p className="summary">{result.match_reason}</p>}
      {result.skills && result.skills.length > 0 && (
        <div className="candidate-skills">
          {result.skills.map((s) => (
            <span key={s} className="skill-chip">
              {s}
            </span>
          ))}
        </div>
      )}
      {result.credibility && (
        <div
          className={`credibility cred-${
            result.credibility.score >= 70
              ? "high"
              : result.credibility.score >= 50
                ? "mid"
                : "low"
          }`}
          title="Signal-validated credibility score"
        >
          <span className="cred-score">Credibility {result.credibility.score}/100</span>
          {result.credibility.flags && result.credibility.flags.length > 0 && (
            <ul className="cred-flags">
              {result.credibility.flags.slice(0, 2).map((f, i) => (
                <li key={i}>⚠ {f}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {result.summary && (
        <details className="candidate-detail">
          <summary>About</summary>
          <p>{result.summary.replace(/^About\s*/i, "")}</p>
        </details>
      )}
      {result.experience && (
        <details className="candidate-detail">
          <summary>Experience ({result.experience.length} chars)</summary>
          <pre>{result.experience}</pre>
        </details>
      )}
      {result.education && (
        <details className="candidate-detail">
          <summary>Education</summary>
          <pre>{result.education.replace(/^Education\s*/i, "")}</pre>
        </details>
      )}
      {result.evidence.length > 0 && (
        <ul className="evidence-list">
          {result.evidence.map((e: Evidence, i: number) => (
            <li key={i}>
              <span className="evidence-field">{e.field}</span>: {e.value}
              {e.source_url && (
                <a href={e.source_url} target="_blank" rel="noreferrer">
                  {" "}
                  source
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {result.gaps.length > 0 && (
        <p className="gaps">
          <strong>Gaps:</strong> {result.gaps.join(", ")}
        </p>
      )}
      <div className="result-actions">
        <button
          className="btn approve"
          onClick={onApprove}
          disabled={verdict === "approved"}
        >
          Approve
        </button>
        <button
          className="btn reject"
          onClick={onReject}
          disabled={verdict === "rejected"}
        >
          Reject
        </button>
        {verdict && (
          <span className={`verdict ${verdict}`}>
            {verdict === "approved" ? "Approved ✓" : "Rejected ✕"}
          </span>
        )}
      </div>
    </article>
  );
}
