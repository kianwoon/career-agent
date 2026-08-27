"use client";

import { useEffect, useRef, useState } from "react";
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
  updateSourceEnabled,
  wizardStart,
  wizardStatus,
  wizardCredentials,
  wizardMfa,
  wizardComplete,
  wizardCancel,
  wizardScreenshotUrl,
  wizardClick,
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
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [wizardBusy, setWizardBusy] = useState<string | null>(null);
  const [wizardHint, setWizardHint] = useState<string | null>(null);
  const [wizardUser, setWizardUser] = useState("");
  const [wizardPass, setWizardPass] = useState("");
  const [wizardMfaCode, setWizardMfaCode] = useState("");
  const [shotUrl, setShotUrl] = useState<string | null>(null);
  const [qrMode, setQrMode] = useState(false);
  const [loginDone, setLoginDone] = useState(false);
  const [shotLoading, setShotLoading] = useState(false);
  const [reloginNeeded, setReloginNeeded] = useState<string | null>(null);
  const [shotError, setShotError] = useState(false);
  const loginDoneRef = useRef(false);
  const __wizLoginPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wizardStartingRef = useRef(false);
  const [takeoverActive, setTakeoverActive] = useState(false);
  const [browserSession, setBrowserSession] = useState<BrowserSessionView | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [history, setHistory] = useState<SearchHistoryItem[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  // Past Searches pagination + search.
  const [historySearch, setHistorySearch] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  // Page size persisted in localStorage so the user's last choice is remembered.
  const [historyPageSize, setHistoryPageSize] = useState(10);
  // Hydration-safe initial state: identical on server and client (no
  // Date.now()/locale formatting during render). The real timestamp is
  // stamped after mount in the effect below.
  const [timeline, setTimeline] = useState<TimelineEvent[]>([
    {
      id: "evt-init",
      kind: "info",
      text: "Agent ready — waiting for a search request.",
      time: "",
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
    // Stamp the initial event's time post-hydration (avoids SSR mismatch).
    setTimeline((prev) =>
      prev.map((e) => (e.id === "evt-init" ? { ...e, time: nowTime() } : e))
    );
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

  /** Derive the active wizard step from the busy key "<id>:<mode>:<flow>". */
  function wizardActiveMode(source: SourceView): "login" | "record" {
    return wizardBusy?.startsWith(`${source.id}:login`) ? "login" : "record";
  }
  function wizardActiveFlow(source: SourceView): "find_jobs" | "find_candidates" {
    return wizardBusy?.includes(":find_candidates") ? "find_candidates" : "find_jobs";
  }

  /** Prepend a timeline event (helper for multi-event blocks). */
  function addEventPrev(kind: TimelineEvent["kind"], text: string) {
    setTimeline((prev) => addEvent(prev, kind, text));
  }

  async function handleWizard(source: SourceView, mode: "login" | "record", flowType?: "find_jobs" | "find_candidates") {
    // Ref guard: state updates async, so two rapid clicks both see stale state.
    if (wizardBusy || wizardStartingRef.current) return;
    wizardStartingRef.current = true;
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

      // Live view: the UI polls the screenshot endpoint via <img> refresh.
      setQrMode(false);
      setLoginDone(false);
      loginDoneRef.current = false;
      setShotLoading(true);
      setShotError(false);
      const url = await wizardScreenshotUrl(source.id, mode);
      setShotUrl(`${url}&t=${Date.now()}`);
      // Record mode: kick off LLM auto-discovery automatically — the user
      // shouldn't need to press Done. Wait a beat so the start call settles.
      if (mode === "record") {
        setTimeline((prev) =>
          addEvent(prev, "action", "Auto-recording: discovering the search box and result cards…")
        );
        await new Promise((r) => setTimeout(r, 4000));
        try {
          const res = await wizardComplete(source.id, "record", query);
          if (res.flow_id) {
            setTimeline((prev) =>
              addEvent(prev, "success", `Flow saved for ${source.name}: ${res.steps.length} steps (card: ${res.card_selectors?.card ?? "n/a"}).`)
            );
          }
        } catch (e2) {
          setTimeline((prev) =>
            addEvent(prev, "warn", `Auto-record failed: ${e2 instanceof Error ? e2.message : e2}`)
          );
        } finally {
          clearWizPolls();
          setWizardBusy(null);
          setShotUrl(null);
          await reloadSources();
        }
        wizardStartingRef.current = false;
        return;
      }

      // Watch for login completion (QR/SSO flows: nothing typed by hand).
      // Only for the login step — record wizards don't need it. Stops itself
      // when the wizard disappears server-side (expired, completed elsewhere,
      // or the API redeployed) so it never spams 404s.
      const lp: ReturnType<typeof setInterval> | null = mode === "login" ? setInterval(async () => {
        try {
          const st = await wizardStatus(source.id, "login");
          if (st.logged_in && !loginDoneRef.current) {
            loginDoneRef.current = true;
            setLoginDone(true);
            setTimeline((prev) =>
              addEvent(prev, "success", "Sign-in detected! Press Done to save this session.")
            );
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          if (msg.includes("404") && lp) {
            // Wizard session no longer exists server-side — stop polling.
            clearInterval(lp);
            __wizLoginPollRef.current = null;
            setTimeline((prev) =>
              addEvent(
                prev,
                "warn",
                "Wizard session expired (server restarted or session completed). Press the Login button to start over."
              )
            );
            setWizardBusy(null);
            setShotUrl(null);
          }
        }
      }, 3000) : null;
      __wizLoginPollRef.current = lp;
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
    } finally {
      wizardStartingRef.current = false;
    }
  }

  function clearWizPolls() {
    const poll = (window as unknown as { __wizPoll?: ReturnType<typeof setInterval> }).__wizPoll;
    if (poll) clearInterval(poll);
    if (__wizLoginPollRef.current) {
      clearInterval(__wizLoginPollRef.current);
      __wizLoginPollRef.current = null;
    }
  }

  async function handleWizardDone(source: SourceView, mode: "login" | "record", flowType?: "find_jobs" | "find_candidates", queryHint?: string) {
    clearWizPolls();
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
    clearWizPolls();
    await wizardCancel(source.id, mode).catch(() => {});
    setWizardBusy(null);
    setWizardHint(null);
    setTimeline((prev) => addEvent(prev, "info", "Wizard cancelled."));
  }

  async function handleWizardCredentials(source: SourceView) {
    if (!wizardUser || !wizardPass) return;
    setWizardBusy(`${source.id}:login:`);
    try {
      const res = await wizardCredentials(source.id, "login", wizardUser, wizardPass);
      if (!res.ok) {
        setTimeline((prev) => addEvent(prev, "warn", `Sign-in failed: ${res.reason ?? "unknown"}`));
      } else if (!res.submitted) {
        setTimeline((prev) => addEvent(prev, "info", "Credentials filled — check the preview, then submit if needed."));
      } else {
        setTimeline((prev) => addEvent(prev, "action", "Credentials submitted. If MFA/OTP is required, enter the code below. Once signed in, press Done."));
      }
      setWizardPass("");
      const url = await wizardScreenshotUrl(source.id, "login");
      setShotUrl(`${url}&t=${Date.now()}`);
    } catch (e) {
      setTimeline((prev) => addEvent(prev, "warn", `Sign-in error: ${e instanceof Error ? e.message : e}`));
    } finally {
      setWizardBusy(null);
    }
  }

  async function handleWizardMfa(source: SourceView) {
    if (!wizardMfaCode) return;
    try {
      const res = await wizardMfa(source.id, "login", wizardMfaCode);
      if (!res.ok) {
        setTimeline((prev) => addEvent(prev, "warn", `MFA failed: ${res.reason ?? "unknown"}`));
      } else {
        setTimeline((prev) => addEvent(prev, "action", "MFA submitted. Once signed in, press Done."));
        setWizardMfaCode("");
      }
      const url = await wizardScreenshotUrl(source.id, "login");
      setShotUrl(`${url}&t=${Date.now()}`);
    } catch (e) {
      setTimeline((prev) => addEvent(prev, "warn", `MFA error: ${e instanceof Error ? e.message : e}`));
    }
  }

  function refreshWizardShot(source: SourceView, zoom: "page" | "qr" = qrMode ? "qr" : "page") {
    wizardScreenshotUrl(source.id, "login", zoom)
      .then((url) => setShotUrl(`${url}&t=${Date.now()}`))
      .catch(() => {});
  }

  // QR codes expire fast: auto-refresh the QR crop while QR mode is on.
  useEffect(() => {
    if (!qrMode || !wizardBusy) return;
    const id = setInterval(() => {
      setShotUrl((cur) => (cur ? `${cur.split("&t=")[0]}&t=${Date.now()}` : cur));
    }, 5000);
    return () => clearInterval(id);
  }, [qrMode, wizardBusy]);
  // Manual refresh shouldn't flash the loading panel for an already-loaded view,
  // and onLoad can be missed (cached imgs) — so auto-clear loading as a failsafe.
  useEffect(() => {
    if (!shotLoading) return;
    const t = setTimeout(() => setShotLoading(false), 6000);
    return () => clearTimeout(t);
  }, [shotLoading]);
  useEffect(() => {
    if (shotUrl) setShotLoading(false);
  }, [shotUrl]);

  /** Persisted include/exclude: PATCHes enabled; server stores the choice. */
  async function handleToggleEnabled(source: SourceView) {
    const next = !source.enabled;
    // Optimistic update, rollback on failure.
    setCustomSources((prev) =>
      prev.map((s) => (s.id === source.id ? { ...s, enabled: next } : s))
    );
    try {
      await updateSourceEnabled(source.id, next);
      setTimeline((prev) =>
        addEvent(
          prev,
          "info",
          `${source.name} ${next ? "enabled" : "disabled"} — ${next ? "included in" : "excluded from"} searches.`
        )
      );
    } catch {
      setCustomSources((prev) =>
        prev.map((s) => (s.id === source.id ? { ...s, enabled: !next } : s))
      );
      setTimeline((prev) =>
        addEvent(prev, "warn", `Could not update ${source.name}.`)
      );
    }
  }

  async function handleDeleteSource(source: SourceView) {
    await deleteSource(source.id).catch(() => {});
    await reloadSources();
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
    setReloginNeeded(null);
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
        // Enabled/disabled is persisted per source (PATCH /sources/{id});
        // the backend runs all enabled sources.
        sources: undefined,
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
        setReloginNeeded("A source session has expired. Re-login, then run the search again.");
        setTimeline((prev) =>
          addEvent(
            prev,
            "warn",
            "Search paused — a source session expired and needs re-login (password or QR scan)."
          )
        );
        return;
      }

      setPhase("completed");
      setResults(response.results ?? []);
      syncSourceFilter(response.results ?? []);
      // Surface per-source failures — prompt re-login for expired sessions.
      for (const issue of response.source_issues ?? []) {
        const expired = /expired|login|sign in/i.test(issue);
        addEventPrev(
          expired ? "warn" : "info",
          expired
            ? `⚠️ ${issue} — open "Add a new source" and press the Login button for this source to re-authenticate.`
            : `Source issue: ${issue}`
        );
      }
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
      // Restore the user's last page-size selection (default 10).
      const saved = Number(localStorage.getItem("historyPageSize"));
      if (saved === 10 || saved === 20 || saved === 30) {
        setHistoryPageSize(saved);
      }
      const resp = await fetchSearchHistory();
      setHistory(resp.items ?? []);
    } catch {
      // Non-fatal — history is a convenience, not core.
    } finally {
      setHistoryLoaded(true);
    }
  }

  // Persist page-size choice and reset to first page whenever it changes.
  function handleHistoryPageSizeChange(size: number) {
    setHistoryPageSize(size);
    setHistoryPage(1);
    try {
      localStorage.setItem("historyPageSize", String(size));
    } catch {
      // localStorage unavailable — selection still applies for this session.
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

      {reloginNeeded && (
        <div className="banner relogin-banner" role="alert">
          <strong>🔑 Re-login required.</strong> {reloginNeeded}
          <button
            className="btn small primary"
            onClick={() => {
              document
                .querySelector(".sources-panel")
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
            }}
          >
            Open sources
          </button>
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
              {customSources.some((s) => s.enabled) && (
                <span className="sources-empty">checked sources are included in searches</span>
              )}
            </div>

            {customSources.length === 0 && (
              <span className="sources-empty">No custom sources yet — add one below.</span>
            )}

            <div className="sources-grid">
              {customSources.map((s) => {
                  const ready = s.has_session && (mode === "jobs" ? s.flows.find_jobs === "active" : s.flows.find_candidates === "active");
                  const enabled = s.enabled;
                  return (
                    <label
                      key={s.id}
                      className={`source-card ${ready ? "ready" : ""} ${enabled ? "selected" : ""} ${isRunning || !s.enabled ? "locked" : ""} ${wizardBusy?.startsWith(`${s.id}:`) ? "wizard-active" : ""}`}
                      title={`${s.domain} — ${enabled ? (ready ? "ready" : "enabled, setup incomplete") : "disabled — checkbox to include"}`}
                    >
                      <div className="source-card-top">
                        <input
                          type="checkbox"
                          className="source-card-check"
                          checked={enabled}
                          onChange={() => handleToggleEnabled(s)}
                          disabled={isRunning}
                          title={enabled ? "Included in searches — uncheck to disable" : "Disabled — check to include in searches"}
                        />
                        <SourceAvatar name={s.name} domain={s.domain} />
                        <div className="source-card-id">
                          <span className="source-card-name">{s.name}</span>
                          <span className="source-card-domain">{s.domain}</span>
                        </div>
                        <button
                          className="source-remove"
                          onClick={(e) => { e.preventDefault(); handleDeleteSource(s); }}
                          aria-label={`Remove ${s.name}`}
                          title={`Remove ${s.name}`}
                        >
                          ×
                        </button>
                      </div>
                      <div className="source-card-status">
                        <span className={`status-pill ${s.has_session ? "ok" : "warn"}`}>
                          {s.has_session ? "✓ signed in" : "⚠ not signed in"}
                        </span>
                        <span className={`status-pill ${s.flows.find_jobs === "active" ? "ok" : "off"}`}>
                          jobs flow {s.flows.find_jobs === "active" ? "✓" : "—"}
                        </span>
                        <span className={`status-pill ${s.flows.find_candidates === "active" ? "ok" : "off"}`}>
                          candidates flow {s.flows.find_candidates === "active" ? "✓" : "—"}
                        </span>
                      </div>
                      {!ready && <span className="source-warn">Setup needed for {mode === "jobs" ? "job" : "candidate"} search</span>}
                      <div className="source-card-actions">
                        <button
                          className="btn small"
                          disabled={!!wizardBusy || isRunning}
                          onClick={(e) => {
                            e.preventDefault();
                            handleWizard(s, "login");
                          }}
                          title={s.has_session ? "Sign in again (session may have expired)" : "Sign in to this site"}
                        >
                          {s.has_session ? "Re-login" : "Login"}
                        </button>
                        {s.has_session && (
                          <>
                            <button
                              className="btn small"
                              disabled={!!wizardBusy || isRunning}
                              onClick={(e) => {
                                e.preventDefault();
                                handleWizard(s, "record", "find_jobs");
                              }}
                              title="Re-record the job-search flow"
                            >
                              {s.flows.find_jobs === "active" ? "Re-record jobs" : "Record jobs"}
                            </button>
                            <button
                              className="btn small"
                              disabled={!!wizardBusy || isRunning}
                              onClick={(e) => {
                                e.preventDefault();
                                handleWizard(s, "record", "find_candidates");
                              }}
                              title="Re-record the candidate-search flow"
                            >
                              {s.flows.find_candidates === "active" ? "Re-record candidates" : "Record candidates"}
                            </button>
                          </>
                        )}
                        <button
                          className="btn small"
                          disabled={!wizardBusy?.startsWith(`${s.id}:`)}
                          onClick={(e) => {
                            e.preventDefault();
                            handleWizardDone(s, wizardActiveMode(s), wizardActiveFlow(s), query);
                          }}
                        >
                          Done
                        </button>
                        <button
                          className="btn small"
                          disabled={!wizardBusy?.startsWith(`${s.id}:`) || wizardBusy === `${s.id}:login:`}
                          onClick={(e) => {
                            e.preventDefault();
                            handleWizardCancel(s, wizardActiveMode(s));
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                      {wizardBusy?.startsWith(`${s.id}:login`) && (
                        <div className="wizard-browser">
                          {loginDone && (
                            <div className="wizard-login-done">✅ Sign-in detected — press Done to save this session.</div>
                          )}
                          {shotLoading && !shotError && !shotUrl && (
                            <div className="wizard-shot-loading">
                              <span className="spinner" aria-hidden /> Loading {s.domain} in the secure browser… this can take up to 30s
                            </div>
                          )}
                          {shotError && (
                            <div className="wizard-shot-loading">
                              Preview unavailable.{" "}
                              <button className="btn small" onClick={(e) => { e.preventDefault(); setShotError(false); setShotLoading(true); refreshWizardShot(s); }}>
                                Retry
                              </button>
                            </div>
                          )}
                          {shotUrl && (
                            /* eslint-disable-next-line @next/next/no-img-element */
                            <img
                              className={`wizard-shot ${qrMode ? "wizard-shot-qr" : ""}`}
                              src={shotUrl}
                              alt="Live browser preview"
                              onLoad={() => { setShotLoading(false); setShotError(false); }}
                              onError={() => { setShotLoading(false); setShotError(true); }}
                              onClick={(e) => {
                                if (qrMode) return; // QR crop: no click-through
                                // click-through: forward coordinates to the backend
                                const rect = (e.target as HTMLImageElement).getBoundingClientRect();
                                const scaleX = 1280 / rect.width;
                                const scaleY = 900 / rect.height;
                                wizardClick(s.id, "login", Math.round((e.clientX - rect.left) * scaleX), Math.round((e.clientY - rect.top) * scaleY))
                                  .then(() => refreshWizardShot(s))
                                  .catch(() => {});
                              }}
                            />
                          )}
                          <div className="wizard-cred-row">
                            <button className="btn small" onClick={(e) => { e.preventDefault(); refreshWizardShot(s); }}>↻ Refresh preview</button>
                            <button
                              className={`btn small ${qrMode ? "primary" : ""}`}
                              onClick={(e) => { e.preventDefault(); const next = !qrMode; setQrMode(next); refreshWizardShot(s, next ? "qr" : "page"); }}
                            >
                              {qrMode ? "📱 QR mode: ON" : "📱 QR code login"}
                            </button>
                            <span className="wizard-hint">
                              {qrMode
                                ? "Auto-refreshing the QR every 5s — scan it with your phone."
                                : "Site uses QR login? Toggle QR mode and scan with your phone."}
                            </span>
                          </div>
                          <div className="wizard-cred-row">
                            <input
                              type="text"
                              value={wizardUser}
                              onChange={(e) => setWizardUser(e.target.value)}
                              placeholder="Username / email"
                              aria-label="Username"
                              autoComplete="off"
                            />
                            <input
                              type="password"
                              value={wizardPass}
                              onChange={(e) => setWizardPass(e.target.value)}
                              placeholder="Password"
                              aria-label="Password"
                              autoComplete="new-password"
                            />
                            <button
                              className="btn small primary"
                              disabled={!!wizardBusy || !wizardUser || !wizardPass}
                              onClick={(e) => { e.preventDefault(); handleWizardCredentials(s); }}
                            >
                              Sign in
                            </button>
                          </div>
                          <div className="wizard-cred-row">
                            <input
                              type="text"
                              value={wizardMfaCode}
                              onChange={(e) => setWizardMfaCode(e.target.value)}
                              placeholder="MFA / OTP code (if asked)"
                              aria-label="MFA code"
                              maxLength={10}
                            />
                            <button
                              className="btn small"
                              disabled={!wizardMfaCode}
                              onClick={(e) => { e.preventDefault(); handleWizardMfa(s); }}
                            >
                              Submit code
                            </button>
                          </div>
                          <span className="wizard-hint">
                            The site runs in a secure browser on the server. Sign in here
                            (credentials are typed into the page, never stored). Handle any
                            CAPTCHA/MFA in the preview by clicking it, then press Done once signed in.
                          </span>
                        </div>
                      )}
                      {wizardBusy?.startsWith(`${s.id}:record`) && (
                        <span className="wizard-hint">
                          Auto-recording in progress: the agent is visiting {s.domain}, searching for
                          "{query || "software engineer"}" and finding the result cards. Watch the activity feed — this takes ~30s.
                        </span>
                      )}
                    </label>
                  );
                })}

                {/* Add-a-source card */}
                <div className="source-card add-source-card">
                  <div className="source-card-top">
                    <span className="source-card-avatar add-avatar" aria-hidden="true">+</span>
                    <div className="source-card-id">
                      <span className="source-card-name">Add a new source</span>
                      <span className="source-card-domain">any job or candidate site</span>
                    </div>
                  </div>
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
                    <button
                      className="btn small primary"
                      onClick={handleAddSource}
                      disabled={wizardBusy === "create" || !newSourceName.trim() || !newSourceUrl.trim()}
                    >
                      {wizardBusy === "create" ? "Adding…" : "Add source"}
                     </button>
                   </div>
                 </div>
               </div>
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
            <HistoryList
              items={history}
              search={historySearch}
              onSearchChange={(v) => {
                setHistorySearch(v);
                setHistoryPage(1);
              }}
              page={historyPage}
              onPageChange={setHistoryPage}
              pageSize={historyPageSize}
              onPageSizeChange={handleHistoryPageSizeChange}
              onSelect={handleViewHistory}
              disabled={sessionBusy}
            />
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

/* ------------------------- Past Searches list --------------------- */

function HistoryList({
  items,
  search,
  onSearchChange,
  page,
  onPageChange,
  pageSize,
  onPageSizeChange,
  onSelect,
  disabled,
}: {
  items: SearchHistoryItem[];
  search: string;
  onSearchChange: (value: string) => void;
  page: number;
  onPageChange: (page: number) => void;
  pageSize: number;
  onPageSizeChange: (size: number) => void;
  onSelect: (item: SearchHistoryItem) => void;
  disabled: boolean;
}) {
  const q = search.trim().toLowerCase();
  const filtered = q
    ? items.filter(
        (item) =>
          item.query.toLowerCase().includes(q) ||
          item.status.toLowerCase().includes(q) ||
          item.type.toLowerCase().includes(q)
      )
    : items;

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const pageItems = filtered.slice(start, start + pageSize);

  // Compact page numbers: show up to 5 pages around the current one.
  const pageNumbers: number[] = [];
  const first = Math.max(1, Math.min(safePage - 2, totalPages - 4));
  for (let p = first; p <= Math.min(first + 4, totalPages); p++) pageNumbers.push(p);

  return (
    <>
      <div className="history-controls">
        <input
          type="search"
          className="history-search"
          placeholder="Search past queries…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="Search past searches"
        />
        <label className="history-page-size">
          Per page
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={30}>30</option>
          </select>
        </label>
      </div>

      {filtered.length === 0 ? (
        <div className="empty small">No matching past searches.</div>
      ) : (
        <>
          <ul className="history-list">
            {pageItems.map((item) => (
              <li key={item.task_id}>
                <button
                  className="history-item"
                  onClick={() => onSelect(item)}
                  disabled={disabled}
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

          {totalPages > 1 && (
            <nav className="history-pagination" aria-label="Past searches pages">
              <button
                className="link-btn"
                disabled={safePage <= 1}
                onClick={() => onPageChange(safePage - 1)}
              >
                ‹ Prev
              </button>
              {first > 1 && <span className="page-ellipsis">…</span>}
              {pageNumbers.map((p) => (
                <button
                  key={p}
                  className={`page-btn${p === safePage ? " current" : ""}`}
                  onClick={() => onPageChange(p)}
                  aria-current={p === safePage ? "page" : undefined}
                >
                  {p}
                </button>
              ))}
              {first + 4 < totalPages && <span className="page-ellipsis">…</span>}
              <button
                className="link-btn"
                disabled={safePage >= totalPages}
                onClick={() => onPageChange(safePage + 1)}
              >
                Next ›
              </button>
              <span className="page-info">
                {start + 1}–{Math.min(start + pageSize, filtered.length)} of {filtered.length}
              </span>
            </nav>
          )}
        </>
      )}
    </>
  );
}

/* ------------------------- Source avatar --------------------------- */

/**
 * Site favicon via Google's public favicon service, falling back to the
 * initials badge when the site has no favicon or the request fails.
 */
function SourceAvatar({ name, domain }: { name: string; domain: string }) {
  const [imgFailed, setImgFailed] = useState(false);
  const showImg = domain && !imgFailed;
  return (
    <span className="source-card-avatar" aria-hidden="true">
      {showImg ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          className="source-card-favicon"
          src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`}
          alt=""
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
      ) : (
        (name || "?").slice(0, 2).toUpperCase()
      )}
    </span>
  );
}
