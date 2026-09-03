"use client";

import { useEffect, useRef, useState } from "react";
import {
  startSearch,
  fetchTaskResults,
  fetchSearchHistory,
  fetchCandidatePlatforms,
  createBrowserSession,
  captureBrowserSession,
  replayBrowserSession,
  refreshBrowserSession,
  listSources,
  agentStatus,
  agentLogin,
  agentSaveSession,
  agentRecord,
  agentRecordFiltersStart,
  agentRecordFiltersStop,
  createSource,
  deleteSource,
  deleteFlow,
  updateSourceEnabled,
  wizardStart,
  wizardStatus,
  wizardCredentials,
  wizardMfa,
  wizardComplete,
  wizardCancel,
  wizardScreenshotUrl,
  wizardClick,
  wizardType,
  wizardKey,
  wizardScroll,
  type SearchMode,
  type SearchResult,
  type Evidence,
  type BrowserSessionView,
  type SearchHistoryItem,
  type SourceView,
} from "@/lib/api";
import { apiBaseUrl } from "@/lib/runtime-config";

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
  // Browser-extension agent connection (runs searches in the user's browser).
  const [agentConnected, setAgentConnected] = useState(false);
  // Onboarding panel shown when the agent is off and the user clicks the chip.
  const [showConnectAgent, setShowConnectAgent] = useState(false);
  // Source awaiting "I'm signed in" confirmation during agent login.
  const [pendingLoginSource, setPendingLoginSource] = useState<SourceView | null>(null);
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
  // Structured sourcing plan (candidate mode) — mirrors the external
  // analysis panel: platform, boolean queries, excludes, salary, location,
  // employment type. Queries/excludes are newline-separated in the UI.
  const [planQueries, setPlanQueries] = useState("");
  const [planExcludes, setPlanExcludes] = useState("");
  const [planSalary, setPlanSalary] = useState("");
  const [planEmploymentType, setPlanEmploymentType] = useState("");
  const [planLocation, setPlanLocation] = useState("Singapore");
  // Multi-platform selection (candidate mode). "LinkedIn" is preselected;
  // the search runs each selected platform and merges the results. The
  // available chip list is fetched from the backend (builtin adapters +
  // sources with an active find_candidates flow).
  const [planPlatforms, setPlanPlatforms] = useState<string[]>(["linkedin"]);
  const [availablePlatforms, setAvailablePlatforms] = useState<string[]>(["linkedin"]);
  // Manual filter recording in progress: "<sourceId>:<flowType>" or null.
  const [recordingFilters, setRecordingFilters] = useState<string | null>(null);
  const togglePlatform = (p: string) =>
    setPlanPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    );
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
    fetchCandidatePlatforms()
      .then((p) => setAvailablePlatforms(p.platforms))
      .catch(() => {});
    // Poll the agent status — updates when the extension connects/disconnects.
    const pollAgent = setInterval(() => {
      agentStatus().then(setAgentConnected).catch(() => setAgentConnected(false));
    }, 5000);
    agentStatus().then(setAgentConnected).catch(() => setAgentConnected(false));
    return () => clearInterval(pollAgent);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function reloadSources() {
    try {
      const srcs = await listSources();
      setCustomSources(srcs);
      fetchCandidatePlatforms()
        .then((p) => setAvailablePlatforms(p.platforms))
        .catch(() => {});
      // Sources refreshed after a re-login — clear the stale "Re-login
      // required" banner unless some source still lacks a session it had
      // before (the banner reappears if a search pauses again anyway).
      if (srcs.every((s) => s.has_session)) {
        setReloginNeeded(null);
      }
    } catch {
      /* ignore */
    }
  }

  async function handleAddSource() {
    if (!newSourceName.trim() || !newSourceUrl.trim()) return;
    // Client-side sanity check: a URL with no dot ("JobStreet") would create
    // a source whose login tab opens https://jobstreet/ — nowhere. Catch the
    // swapped-fields typo here where we can say exactly what went wrong.
    const rawUrl = newSourceUrl.trim().replace(/^https?:\/\//i, "");
    if (!rawUrl.includes(".")) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Add source failed: "${newSourceUrl.trim()}" doesn't look like a site URL — put the address (e.g. sg.jobstreet.com) in the URL field and the display name in the Name field.`)
      );
      return;
    }
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
          const msg = e2 instanceof Error ? e2.message : String(e2);
          if (msg.includes("404")) {
            // Session vanished server-side: either completed by another path
            // or lost (backend restart/redeploy wipes in-memory wizards).
            // Check whether the flow actually saved before reporting.
            await reloadSources();
            let saved = false;
            setCustomSources((cur) => {
              saved = cur.some((c) => c.id === source.id && c.flows[flowType === "find_candidates" ? "find_candidates" : "find_jobs"] === "active");
              return cur;
            });
            if (saved) {
              setTimeline((prev) =>
                addEvent(prev, "success", `Flow saved for ${source.name} (completed by another action).`)
              );
            } else {
              setTimeline((prev) =>
                addEvent(prev, "warn", `Record session lost (backend restarted?) — press "Record ${flowType === "find_candidates" ? "candidates" : "jobs"}" to try again.`)
              );
            }
          } else {
            setTimeline((prev) =>
              addEvent(prev, "warn", `Auto-record failed: ${msg}`)
            );
          }
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
    // Keep the wizard key set: the browser session is still live and the
    // in-card preview must stay mounted until Done/Cancel ends the wizard.
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

  /** Re-login via the browser agent: open the site's login page in a tab,
   * the user signs in there, then capture cookies. */
  async function handleAgentLogin(source: SourceView) {
    if (!agentConnected) return;
    setWizardBusy(`${source.id}:agent-login`);
    try {
      await agentLogin(source.id);
      setTimeline((prev) =>
        addEvent(prev, "action", `Opened ${source.domain} sign-in in your browser. Sign in there, then press "I'm signed in" here.`)
      );
      // Give the user time to sign in; capture on their confirmation.
      setPendingLoginSource(source);
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Agent login failed: ${e instanceof Error ? e.message : e}`)
      );
    } finally {
      setWizardBusy(null);
    }
  }

  /** Capture cookies after the user says they've signed in. */
  async function handleAgentCaptureSession(source: SourceView) {
    setWizardBusy(`${source.id}:agent-capture`);
    try {
      const updated = await agentSaveSession(source.id);
      if (updated.has_session) {
        setTimeline((prev) => addEvent(prev, "success", `Session saved for ${source.name} (from your browser).`));
      } else {
        setTimeline((prev) => addEvent(prev, "warn", `No cookies captured for ${source.name} — sign in first, then retry.`));
      }
      await reloadSources();
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Session capture failed: ${e instanceof Error ? e.message : e}`)
      );
    } finally {
      setWizardBusy(null);
      setPendingLoginSource(null);
    }
  }

  /** Auto-record a flow in the user's browser via the agent. */
  async function handleAgentRecord(source: SourceView, flowType: "find_jobs" | "find_candidates") {
    if (!agentConnected) return;
    setWizardBusy(`${source.id}:record:${flowType}`);
    setTimeline((prev) =>
      addEvent(prev, "action", `Recording ${flowType === "find_jobs" ? "jobs" : "candidates"} flow in your browser (a tab will search "${query || "software engineer"}")…`)
    );
    try {
      await agentRecord(source.id, flowType, query || "software engineer");
      setTimeline((prev) => addEvent(prev, "success", `Flow saved for ${source.name} — recorded in your browser.`));
      await reloadSources();
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Agent record failed: ${e instanceof Error ? e.message : e}`)
      );
    } finally {
      setWizardBusy(null);
    }
  }

  /** Manual filter recording: capture the user's filter-panel clicks and
   * merge them into the flow so every search replays those filters. */
  async function handleRecordFilters(source: SourceView, flowType: "find_jobs" | "find_candidates", phase: "start" | "stop") {
    if (!agentConnected) return;
    setWizardBusy(`${source.id}:filters:${phase}`);
    try {
      if (phase === "start") {
        await agentRecordFiltersStart(source.id);
        setRecordingFilters(`${source.id}:${flowType}`);
        setTimeline((prev) =>
          addEvent(prev, "action", `Recording filters for ${source.name} — in the agent tab, run a search, click the filter options you want, then press "Done recording filters" here.`)
        );
      } else {
        await agentRecordFiltersStop(source.id, flowType);
        setTimeline((prev) => addEvent(prev, "success", `Filters saved for ${source.name} — they'll be applied on every ${flowType === "find_jobs" ? "job" : "candidate"} search.`));
        await reloadSources();
      }
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Filter recording failed: ${e instanceof Error ? e.message : e}`)
      );
    } finally {
      setWizardBusy(null);
      if (phase === "stop") setRecordingFilters(null);
    }
  }

  async function handleDeleteSource(source: SourceView) {
    await deleteSource(source.id).catch(() => {});
    await reloadSources();
    setTimeline((prev) => addEvent(prev, "info", `Source removed: ${source.name}.`));
  }

  async function handleDeleteFlow(source: SourceView, flowType: "find_jobs" | "find_candidates") {
    try {
      await deleteFlow(source.id, flowType);
      await reloadSources();
      setTimeline((prev) =>
        addEvent(prev, "info", `${flowType === "find_jobs" ? "Jobs" : "Candidates"} flow removed for ${source.name}.`)
      );
    } catch (e) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Could not remove flow: ${e instanceof Error ? e.message : e}`)
      );
    }
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
    if (isRunning) return;
    const queries = planQueries
      .split("\n")
      .map((q) => q.trim())
      .filter(Boolean);
    // Mirror backend caps (MAX_PLAN_QUERIES/MAX_PLAN_EXCLUDES) so the user
    // gets an inline message instead of a 422 after the task starts.
    if (queries.length > 5) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Too many queries: ${queries.length}. Use at most 5 boolean queries (one per line) — merge related ones with OR.`)
      );
      return;
    }
    const excludesEarly = planExcludes
      .split(/[,\n]/)
      .map((e) => e.trim())
      .filter(Boolean);
    if (excludesEarly.length > 10) {
      setTimeline((prev) =>
        addEvent(prev, "warn", `Too many exclude terms: ${excludesEarly.length}. Use at most 10.`)
      );
      return;
    }
    if (mode === "candidates" && queries.length > 0) {
      // Plan mode: no single query needed.
    } else if (!query.trim()) {
      return;
    }
    setReloginNeeded(null);
    setPhase("running");
    setResults([]);
    setVerdicts({});
    setTaskId(null);
    const label =
      mode === "candidates" && queries.length > 0
        ? `${queries.length} plan queries`
        : `"${query.trim()}"`;
    setTimeline((prev) =>
      addEvent(
        prev,
        "action",
        `Search started (${mode === "jobs" ? "jobs" : "candidates"}): ${label}.`
      )
    );

    try {
      // Start the task on the backend.
      const excludes = planExcludes
        .split(/[,\n]/)
        .map((e) => e.trim())
        .filter(Boolean);
      const hasPlan = mode === "candidates" && (queries.length > 0 || excludes.length > 0);
      const task = await startSearch({
        query: query.trim() || (queries.length > 0 ? queries[0] : ""),
        mode,
        location:
          mode === "candidates" && planLocation.trim()
            ? planLocation.trim()
            : "Singapore",
        // Enabled/disabled is persisted per source (PATCH /sources/{id});
        // the backend runs all enabled sources.
        sources: undefined,
        plan: hasPlan
          ? {
              queries: queries.length > 0 ? queries : undefined,
              exclude: excludes.length > 0 ? excludes : undefined,
              platforms: planPlatforms.length > 0 ? planPlatforms : ["linkedin"],
              salary: planSalary.trim() || undefined,
              employment_type: planEmploymentType.trim() || undefined,
            }
          : undefined,
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
            {mode === "jobs" && (
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRunSearch();
                }}
                placeholder={PLACEHOLDER_QUERY}
                disabled={isRunning}
                aria-label="Search query"
              />
            )}
            <button
              className="btn primary"
              onClick={handleRunSearch}
              disabled={isRunning || (mode === "jobs" && !query.trim()) || (mode === "candidates" && !planQueries.trim())}
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

          {/* ---------- Sourcing plan panel (candidate mode, always on) ---------- */}
          {mode === "candidates" && (
            <div className="plan-panel" role="group" aria-label="Sourcing plan">
              <div className="plan-grid">
                <div className="plan-field" role="group" aria-label="Platforms">
                  <span>Platforms</span>
                  <div className="platform-chips">
                    {availablePlatforms.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className={`platform-chip${planPlatforms.includes(p) ? " selected" : ""}`}
                        onClick={() => togglePlatform(p)}
                        disabled={isRunning}
                        aria-pressed={planPlatforms.includes(p)}
                      >
                        {p === "linkedin" ? "LinkedIn" : p.charAt(0).toUpperCase() + p.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>
                <label className="plan-field">
                  <span>Location</span>
                  <input
                    type="text"
                    value={planLocation}
                    onChange={(e) => setPlanLocation(e.target.value)}
                    placeholder="Singapore"
                    disabled={isRunning}
                  />
                </label>
                <label className="plan-field plan-field-wide">
                  <span>Role / context (optional fallback query)</span>
                  <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder='e.g. "Senior Executive, Agency Accounting" — Ocean Network Express'
                    disabled={isRunning}
                  />
                </label>
                <label className="plan-field plan-field-wide">
                  <span>
                    Boolean queries (one per line, max 5)
                    {planQueries.split("\n").filter((q) => q.trim()).length > 5 && (
                      <em style={{ color: "var(--warn, #e0a300)", marginLeft: 6 }}>
                        — {planQueries.split("\n").filter((q) => q.trim()).length} entered, over the limit
                      </em>
                    )}
                  </span>
                  <textarea
                    rows={4}
                    value={planQueries}
                    onChange={(e) => setPlanQueries(e.target.value)}
                    placeholder={'"agency accounting" AND ("AP" OR "accounts payable")\n"agency accounting" AND ("insurance" OR "real estate")'}
                    disabled={isRunning}
                  />
                </label>
                <label className="plan-field plan-field-wide">
                  <span>Exclude terms (comma or newline, max 10)</span>
                  <textarea
                    rows={2}
                    value={planExcludes}
                    onChange={(e) => setPlanExcludes(e.target.value)}
                    placeholder="intern, student, fresh graduate, audit manager"
                    disabled={isRunning}
                  />
                </label>
                <label className="plan-field">
                  <span>Salary (ranking criteria)</span>
                  <input
                    type="text"
                    value={planSalary}
                    onChange={(e) => setPlanSalary(e.target.value)}
                    placeholder="SGD 5,200/month max + 1 month bonus"
                    disabled={isRunning}
                  />
                </label>
                <label className="plan-field">
                  <span>Employment type</span>
                  <input
                    type="text"
                    value={planEmploymentType}
                    onChange={(e) => setPlanEmploymentType(e.target.value)}
                    placeholder="Contract, 2 years fixed-term"
                    disabled={isRunning}
                  />
                </label>
              </div>
              <p className="plan-hint">
                Queries run sequentially, merged and deduplicated; excludes
                become NOT (...) clauses and a result filter; location is
                applied as a post-filter. The simple search box above is
                hidden in candidate mode — use Role / context instead.
              </p>
            </div>
          )}

          {/* ---------------- Sources panel ---------------- */}
          <div className="sources-panel">
            <div className="sources-row">
              <span className="sources-label">Sources:</span>
              <button
                type="button"
                className={`agent-chip ${agentConnected ? "on" : "off"}`}
                title={
                  agentConnected
                    ? "Browser extension connected — searches run in your browser (no blocks)"
                    : "Extension not connected — click for setup steps"
                }
                onClick={() => {
                  if (!agentConnected) setShowConnectAgent((v) => !v);
                  else setShowConnectAgent(false);
                }}
                aria-expanded={showConnectAgent}
              >
                <span className="agent-dot" aria-hidden="true" />
                {agentConnected ? "Browser agent: ON" : "Browser agent: off"}
              </button>
              {!agentConnected && !showConnectAgent && (
                <button type="button" className="link-btn" onClick={() => setShowConnectAgent(true)}>
                  Connect it →
                </button>
              )}
              {customSources.some((s) => s.enabled) && (
                <span className="sources-empty">checked sources are included in searches</span>
              )}
            </div>

            {showConnectAgent && !agentConnected && (
              <ConnectBrowserAgent onClose={() => setShowConnectAgent(false)} />
            )}

            {customSources.length === 0 && (
              <span className="sources-empty">No custom sources yet — add one below.</span>
            )}

            <div className="sources-grid">
              {customSources.map((s) => {
                  const ready = s.has_session && (mode === "jobs" ? s.flows.find_jobs === "active" : s.flows.find_candidates === "active");
                  const enabled = s.enabled;
                  return (
                    <div
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
                          {s.flows.find_jobs === "active" && !isRunning && (
                            <button
                              className="flow-remove"
                              aria-label="Remove jobs flow"
                              title="Remove jobs flow (record again to restore)"
                              disabled={isRunning}
                              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteFlow(s, "find_jobs"); }}
                            >
                              ×
                            </button>
                          )}
                        </span>
                        <span className={`status-pill ${s.flows.find_candidates === "active" ? "ok" : "off"}`}>
                          candidates flow {s.flows.find_candidates === "active" ? "✓" : "—"}
                          {s.flows.find_candidates === "active" && !isRunning && (
                            <button
                              className="flow-remove"
                              aria-label="Remove candidates flow"
                              title="Remove candidates flow (record again to restore)"
                              disabled={isRunning}
                              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDeleteFlow(s, "find_candidates"); }}
                            >
                              ×
                            </button>
                          )}
                        </span>
                      </div>
                      {!ready && <span className="source-warn">Setup needed for {mode === "jobs" ? "job" : "candidate"} search</span>}
                      {/* Only offer capture while the session is still missing —
                          once has_session flips true (e.g. captured, or restored
                          from an earlier run) the button would be a dead end. */}
                      {pendingLoginSource?.id === s.id && !s.has_session && (
                        <button
                          className="btn small primary"
                          disabled={!!wizardBusy}
                          onClick={(e) => {
                            e.preventDefault();
                            handleAgentCaptureSession(s);
                          }}
                        >
                          ✓ I'm signed in — capture session
                        </button>
                      )}
                      <div className="source-card-actions">
                        {agentConnected ? (
                          <>
                            <button
                              className="btn small"
                              disabled={!!wizardBusy || isRunning}
                              onClick={(e) => {
                                e.preventDefault();
                                handleAgentLogin(s);
                              }}
                              title="Open sign-in in your browser, then capture the session"
                            >
                              {s.has_session ? "Re-login" : "Login"}
                            </button>
                            <button
                              className="btn small"
                              disabled={!!wizardBusy || isRunning}
                              onClick={(e) => {
                                e.preventDefault();
                                handleAgentRecord(s, "find_jobs");
                              }}
                              title="Discover the job-search flow in your browser"
                            >
                              {s.flows.find_jobs === "active" ? "Re-record jobs" : "Record jobs"}
                            </button>
                            <button
                              className="btn small"
                              disabled={!!wizardBusy || isRunning}
                              onClick={(e) => {
                                e.preventDefault();
                                handleAgentRecord(s, "find_candidates");
                              }}
                              title="Discover the candidate-search flow in your browser"
                            >
                              {s.flows.find_candidates === "active" ? "Re-record candidates" : "Record candidates"}
                            </button>
                            {recordingFilters === `${s.id}:find_jobs` ? (
                              <button
                                className="btn small primary"
                                disabled={!!wizardBusy}
                                onClick={(e) => {
                                  e.preventDefault();
                                  handleRecordFilters(s, "find_jobs", "stop");
                                }}
                                title="Save the filter clicks you just made into the flow"
                              >
                                ✓ Done recording filters
                              </button>
                            ) : (
                              <button
                                className="btn small"
                                disabled={!!wizardBusy || isRunning || (!s.flows.find_jobs && !s.flows.find_candidates)}
                                onClick={(e) => {
                                  e.preventDefault();
                                  // Attach filters to whichever flow exists (jobs preferred).
                                  handleRecordFilters(s, s.flows.find_jobs ? "find_jobs" : "find_candidates", "start");
                                }}
                                title="Record filter-panel clicks (Industry, Salary, Work Type…) and apply them on every search"
                              >
                                Record filters
                              </button>
                            )}
                          </>
                        ) : (
                          <>
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
                          </>
                        )}
                        {!agentConnected && (
                          <>
                            <button
                              className="btn small"
                              disabled={wizardBusy !== `${s.id}:login:` || !wizardBusy}
                              onClick={(e) => {
                                e.preventDefault();
                                handleWizardDone(s, wizardActiveMode(s), wizardActiveFlow(s), query);
                              }}
                              title={wizardBusy === `${s.id}:login:` ? "Save this session" : "Auto-record completes on its own — no Done needed"}
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
                          </>
                        )}
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
                          <RemoteControlRow
                            sourceId={s.id}
                            onAction={async (act) => {
                              if (act.kind === "type") await wizardType(s.id, "login", act.text ?? "");
                              else if (act.kind === "key") await wizardKey(s.id, "login", act.key ?? "Enter");
                              else if (act.kind === "scroll") await wizardScroll(s.id, "login", 640, 450, act.deltaY ?? 400);
                              refreshWizardShot(s);
                            }}
                          />
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
                              disabled={(!wizardBusy?.startsWith(`${s.id}:`) && !!wizardBusy) || !wizardUser || !wizardPass}
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
                    </div>
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
            {result.source_url && (
              <a
                className="profile-link"
                href={result.source_url}
                target="_blank"
                rel="noreferrer"
                title="Open profile in a new tab"
              >
                View profile ↗
              </a>
            )}
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

/* ------------------- Connect-browser-agent onboarding ---------------- */

/**
 * Setup steps shown when the extension agent isn't connected. A normal user
 * otherwise has no way to discover that searches depend on a browser
 * extension — this explains install + config and auto-detects the API URL
 * they should paste into the extension popup.
 */
function ConnectBrowserAgent({ onClose }: { onClose: () => void }) {
  const [apiUrl, setApiUrl] = useState("…");
  useEffect(() => {
    apiBaseUrl().then(setApiUrl).catch(() => setApiUrl("http://localhost:8000"));
  }, []);
  return (
    <div className="connect-agent-panel" role="dialog" aria-label="Connect the browser agent">
      <div className="connect-agent-head">
        <strong>Connect the browser agent</strong>
        <button type="button" className="link-btn" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <p className="connect-agent-why">
        Without it, searches run server-side and sites like LinkedIn usually block them.
      </p>
      <ol className="connect-agent-steps">
        <li>
          <strong>Install the extension</strong> in Chrome — see{" "}
          <code>extension/INSTALL.md</code> in the project (load unpacked via{" "}
          <code>chrome://extensions</code>, Developer mode → Load unpacked).
        </li>
        <li>
          <strong>Point it at this server:</strong> click the Career Agent icon in
          your toolbar and set the API URL to
          <span className="connect-agent-url"> {apiUrl}</span>
          <button
            type="button"
            className="btn small"
            onClick={() => navigator.clipboard?.writeText(apiUrl)}
            title="Copy API URL"
          >
            Copy
          </button>
        </li>
        <li>
          <strong>Wait for the chip</strong> — it turns <span className="ok">Browser agent: ON</span>{" "}
          within a few seconds (the extension checks in every ~2.5s).
        </li>
      </ol>
    </div>
  );
}

/* ------------------------- Wizard remote control -------------------- */

type RemoteAction =
  | { kind: "type"; text: string }
  | { kind: "key"; key: string }
  | { kind: "scroll"; deltaY: number };

/**
 * Keyboard/scroll controls for the wizard preview. Click the preview to focus
 * a field (server-side click), then type here — text mirrors to the remote
 * browser. Covers CAPTCHA, OTP, MFA, any interactive login step.
 */
function RemoteControlRow({
  sourceId,
  onAction,
}: {
  sourceId: string;
  onAction: (action: RemoteAction) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showId, setShowId] = useState(`rc-${sourceId}`);

  async function run(action: RemoteAction) {
    setBusy(true);
    try {
      await onAction(action);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wizard-cred-row remote-control-row">
      <input
        key={showId}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={async (e) => {
          if (e.key === "Enter" && text.trim()) {
            e.preventDefault();
            await run({ kind: "type", text });
            setText("");
          }
        }}
        placeholder="Click a field in the preview, then type here →"
        aria-label="Remote type into browser"
        disabled={busy}
      />
      <button
        className="btn small primary"
        disabled={busy || !text.trim()}
        onClick={async (e) => {
          e.preventDefault();
          await run({ kind: "type", text });
          setText("");
        }}
      >
        Type
      </button>
      <button
        className="btn small"
        disabled={busy}
        onClick={async (e) => {
          e.preventDefault();
          await run({ kind: "key", key: "Enter" });
        }}
      >
        ⏎ Enter
      </button>
      <button
        className="btn small"
        disabled={busy}
        onClick={async (e) => {
          e.preventDefault();
          await run({ kind: "key", key: "Tab" });
        }}
      >
        Tab
      </button>
      <button
        className="btn small"
        disabled={busy}
        onClick={async (e) => {
          e.preventDefault();
          await run({ kind: "scroll", deltaY: 400 });
        }}
        title="Scroll the remote page down"
      >
        ↓
      </button>
      <button
        className="btn small"
        disabled={busy}
        onClick={async (e) => {
          e.preventDefault();
          await run({ kind: "scroll", deltaY: -400 });
        }}
        title="Scroll the remote page up"
      >
        ↑
      </button>
    </div>
  );
}
