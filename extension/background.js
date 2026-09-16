/**
 * Career Agent — background service worker (HTTP polling edition).
 *
 * MV3 suspends idle service workers and kills WebSockets — unreliable.
 * Polling is platform-reliable: every HTTP request wakes the worker.
 *
 * Loop: poll /agent/poll → execute any command → POST /agent/result → repeat.
 */

const DEFAULT_API = "https://career-agent-kianwoon-88223cd5.koyeb.app";
const POLL_INTERVAL_MS = 2500; // idle polling
const ACTIVE_POLL_MS = 300; // fast polling while a flow runs

let API_BASE = DEFAULT_API;
let busy = false;
// Watchdog for the busy flag: a leaked command promise (tab closed mid-
// chrome.scripting.executeScript, hung SPA) never settles, .finally() never
// runs, and busy would stay true forever — every later command instantly
// rejected "agent busy" (seen live 2026-09-04). The longest legit command is
// a 5-query LinkedIn plan (~2-3 min); 4 min clears any real straggler.
// MUST exceed the longest server dispatch timeout (linkedin_people_plan
// 450s) or the watchdog kills a legitimately-running command (3ce3caba).
const COMMAND_WATCHDOG_MS = 8 * 60 * 1000;
let currentCmdStartedAt = 0;
let currentCmdAction = "";
// Identifies this service-worker instance; sent with every poll so the
// backend can instantly fail commands orphaned by a worker reload instead
// of letting them burn their full dispatch timeout.
const BOOT_ID = `boot-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
let loopTimer = null;
// The tab the agent opened for its own work — all fill/click/extract target
// THIS tab, never whatever the user happens to have focused.
let agentTabId = null;
// Manual click-recording state. The recorder lives in the page (MAIN world)
// and is DESTROYED on hard navigation, so events are mirrored to
// chrome.storage.session and the flag survives so tabs.onUpdated can
// re-inject after a reload. See cmdStartRecord / cmdStopRecord.
let recordingActive = false;
const REC_ACTIVE_KEY = "caRecordingActive";
const REC_EVENTS_KEY = "caRecordEvents";

// Master on/off switch (popup toggle). When false the agent stays connected
// to nothing: pollOnce() short-circuits so no command is ever fetched, but the
// loop keeps rescheduling so flipping it back on resumes instantly.
let AGENT_ENABLED = true;

async function loadConfig() {
  const stored = await chrome.storage.local.get(["apiBase", "enabled"]);
  API_BASE = (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
  AGENT_ENABLED = stored.enabled !== false;
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#2e9e5b" : "#999" });
}

// --- command execution (unchanged semantics) ------------------------------

// Bring the agent tab (and its window) to the foreground. Used when the agent
// needs the USER to interact — e.g. Re-login / session-expiry. Never throws.
async function activateTab(tabId) {
  try {
    await chrome.tabs.update(tabId, { active: true });
    const t = await chrome.tabs.get(tabId);
    if (t && t.windowId !== undefined) {
      await chrome.windows.update(t.windowId, { focused: true });
    }
  } catch {
    /* focus is best-effort — never fail the command on it */
  }
}

async function ensureTab(url, { activate = false } = {}) {
  // Open agent navigation in the agent-owned tab — never hijack the user's
  // current tab (it may be the app itself, or anything else). Background by
  // default so scraping/search flows don't steal focus; callers that need the
  // user to see the page (Re-login) pass { activate: true }.
  // Reuse the existing agent tab if it's still open (one workspace per agent).
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      if (t && t.id !== undefined) {
        // Attach the completion listener BEFORE updating the URL so we can't
        // miss the "complete" event, then navigate. Activation runs (and is
        // awaited) immediately after update — before we block on the load
        // promise — so the tab is foregrounded the moment navigation starts.
        const loadP = waitForComplete(agentTabId); // resolves true/false, never throws
        await chrome.tabs.update(agentTabId, { url });
        if (activate) await activateTab(agentTabId);
        await loadP;
        return agentTabId;
      }
    } catch {
      agentTabId = null; // tab was closed — fall through and create a new one
    }
  }
  const tab = await chrome.tabs.create({ active: activate, url });
  agentTabId = tab.id;
  if (activate) await activateTab(tab.id); // foreground before blocking on load
  await waitForComplete(tab.id);
  return tab.id;
}

// Resolves true once the tab reaches status "complete", or false on timeout.
// Non-fatal by design: callers proceed regardless (their steps guard the DOM).
// The listener is always removed on settle (complete OR timeout).
function waitForComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const finish = (ok) => {
      clearTimeout(t);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(ok);
    };
    const t = setTimeout(() => finish(false), timeoutMs);
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") finish(true);
    };
    // Short-circuit: if the tab is already fully loaded, resolve immediately.
    chrome.tabs
      .get(tabId)
      .then((tab) => {
        if (tab && tab.status === "complete") finish(true);
      })
      .catch(() => {
        /* tab may be gone; let the timeout path settle it */
      });
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// NOTE: there is deliberately no "active tab" concept anywhere in this agent.
// All navigation and script execution happens in the agent-owned tab.

async function execOnTab(fn, args = [], world = "MAIN") {
  // Always target the agent's own tab. If it doesn't exist yet, create it
  // on the site first — the agent NEVER touches the user's focused tab.
  let tabId;
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      tabId = t && t.id;
    } catch {
      agentTabId = null;
    }
  }
  if (!tabId) tabId = await ensureTab("about:blank");
  const res = await chrome.scripting.executeScript({
    target: { tabId },
    world,
    func: fn,
    args,
  });
  return res && res[0] && res[0].result;
}

// waitForPageReady — poll until the document is actually usable instead of
// trusting fixed sleeps (slow renders made the next action hit a half-built
// page: empty-state extraction, fill on a missing input). Criteria:
// document.complete AND no ongoing fetch/XHR burst AND DOM stable.
async function waitForPageReady(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const s = await execOnTab(() => ({
      complete: document.readyState === "complete",
      pending: performance.getEntriesByType("resource").filter(
        (e) => e.responseEnd === 0
      ).length,
      domLen: document.body ? document.body.innerHTML.length : 0,
    })).catch(() => null);
    if (s && s.complete && s.pending === 0) {
      // DOM-settle beat: two identical innerHTML lengths a beat apart.
      await sleep(600);
      const s2 = await execOnTab(() => (document.body ? document.body.innerHTML.length : 0)).catch(() => 0);
      if (s2 === s.domLen) return true;
    }
    await sleep(400);
  }
  return false; // proceed anyway — caller steps have their own guards
}

async function cmdNavigate(url, { activate = false } = {}) {
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  await ensureTab(url, { activate });
  await waitForPageReady(); // replaces the blind 2.5s SPA beat
  return { url };
}

async function waitForElement(selector, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await execOnTab((sel) => !!document.querySelector(sel), [selector]).catch(() => false);
    if (found) return true;
    await sleep(300);
  }
  return false;
}

async function cmdFill(selector, text) {
  // Wait for the input to exist before typing — slow SPA renders used to
  // make the fill throw "Element not found" even though the field appears
  // a second later.
  await waitForElement(selector);
  const r = await execOnTab((sel, txt) => {
    const el = document.querySelector(sel);
    if (!el) return { ok: false, error: "Element not found: " + sel };
    el.focus();
    el.scrollIntoView({ block: "center" });
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, txt);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  }, [selector, String(text ?? "")]);
  if (!r || !r.ok) throw new Error((r && r.error) || "fill failed");
  return r;
}

async function cmdClick(selector) {
  await waitForElement(selector);
  const r = await execOnTab((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { ok: false, error: "Element not found: " + sel };
    el.scrollIntoView({ block: "center" });
    el.click();
    return { ok: true };
  }, [selector]);
  if (!r || !r.ok) throw new Error((r && r.error) || "click failed");
  await waitForPageReady(8000); // clicked pages usually navigate/re-render
  return r;
}

async function cmdPress(key) {
  await execOnTab((k) => {
    const el = document.activeElement || document.body;
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
    if (k === "Enter" && el.form) {
      el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    }
    el.dispatchEvent(new KeyboardEvent("keyup", { key: k, bubbles: true }));
  }, [key]);
  await waitForPageReady(12000); // search submit re-renders the page
  return { ok: true, key };
}

async function cmdExtract(cardSelector, fields, maxItems) {
  return (
    (await execOnTab((card, fieldMap, max) => {
      const cards = document.querySelectorAll(card);
      const out = [];
      for (const c of Array.from(cards).slice(0, max || 30)) {
        const pick = (sel) => {
          if (!sel) return "";
          const el = c.querySelector(sel);
          return el ? (el.textContent || "").trim().slice(0, 300) : "";
        };
        // URL pick: prefer deep links (profiles/candidates/jobs/<id>) over
        // search/listing wrapper hrefs (e.g. SEEK /talentsearch/keyword?...searchQuery=).
        const isWrapperHref = (h) =>
          /\/keyword\b/.test(h) || /searchQuery=/.test(h) || /searchId=/.test(h);
        const isDeepHref = (h) => /\/(profiles?|candidates?|jobs?)\/[0-9a-f-]{8,}/i.test(h);
        const hrefs = Array.from(c.querySelectorAll("a"))
          .map((a) => a.href)
          .filter(Boolean);
        const deepHref = hrefs.find(isDeepHref);
        const plainHref = hrefs.find((h) => !isWrapperHref(h));
        const url =
          deepHref || plainHref || hrefs[0] || c.href || "";
        // Title pick: fall back to the card's first substantial text line
        // (skip 1-2 char nodes like avatar initials / truncated badges).
        let title = pick(fieldMap && fieldMap.title);
        if (!title) {
          const lines = (c.innerText || "")
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
          title = (lines.find((l) => l.length >= 3) || "").slice(0, 300);
        }
        out.push({
          title,
          company: pick(fieldMap && fieldMap.company),
          location: pick(fieldMap && fieldMap.location),
          summary: pick(fieldMap && fieldMap.summary),
          // Prefer a child link; fall back to the card itself being one
          // (e.g. <a class="result-card">…</a> — querySelector misses that).
          url,
          raw_text: (c.innerText || "").slice(0, 500),
        });
      }
      return out;
    }, [cardSelector, fields || {}, maxItems || 30])) || []
  );
}

async function cmdGetCookies(url) {
  // Query by URL (not domain) so parent/apex cookies the browser would SEND to
  // this page are included — e.g. MCF sets its session on .mycareersfuture.gov.sg
  // while base_url is https://www.mycareersfuture.gov.sg/.
  const cookies = await chrome.cookies.getAll({ url });
  return cookies.map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path,
    expires: c.expirationDate || -1,
    httpOnly: c.httpOnly,
    secure: c.secure,
    // Playwright only accepts Strict|Lax|None. Chrome's cookies API returns
    // "unspecified" | "lax" | "strict" | "no_restriction" — normalize all of them.
    sameSite: c.sameSite === "no_restriction" || c.sameSite === "none"
      ? "None"
      : c.sameSite === "strict"
        ? "Strict"
        : "Lax", // covers "lax" and "unspecified"
  }));
}

async function cmdClearCookies(url) {
  // Best-effort wipe of all cookies for a site's domain so a re-login starts
  // from a clean slate (lets the user switch accounts). Never throws fatally;
  // returns however many removes succeeded.
  const u = new URL(url);
  // Enumerate by URL so parent/apex cookies are cleared too (see cmdGetCookies).
  const cookies = await chrome.cookies.getAll({ url });
  let cleared = 0;
  for (const c of cookies) {
    try {
      const scheme = c.secure ? "https://" : "http://";
      const host = (c.domain || u.hostname).replace(/^\./, "");
      const cookieUrl = `${scheme}${host}${c.path || "/"}`;
      const res = await chrome.cookies.remove({ url: cookieUrl, name: c.name });
      if (res) cleared += 1;
    } catch (err) {
      // ignore individual failures — best-effort
    }
  }
  return { cleared };
}

async function cmdRunFlow(baseUrl, query, steps) {
  const results = [];
  for (const step of steps || []) {
    const action = step.action;
    if (action === "navigate") {
      // URL templates may contain {query} — substitute the search text.
      const url = (step.url || baseUrl).replaceAll("{query}", encodeURIComponent(query || ""));
      await cmdNavigate(url);
    }
    else if (action === "fill") await cmdFill(step.selector, step.param === "query" ? query : step.value || "");
    else if (action === "click") await cmdClick(step.selector);
    else if (action === "press") await cmdPress(step.key || "Enter");
    else if (action === "wait") await sleep((step.seconds || 2) * 1000);
    else if (action === "card" || step.card) {
      // Session-expiry guard: if the results page is actually a login wall,
      // report needs_human so the search PAUSES for re-login instead of
      // silently returning "no results".
      const wall = await execOnTab(() => {
        const hasPw = !!document.querySelector("input[type='password']");
        const t = (document.body?.innerText || "").slice(0, 400).toLowerCase();
        return hasPw || /sign in|log in|authwall|join linkedin|sign up/.test(t);
      });
      if (wall) {
        try { if (agentTabId !== null) { await chrome.tabs.update(agentTabId, { active: true }); const t = await chrome.tabs.get(agentTabId); if (t && t.windowId !== undefined) await chrome.windows.update(t.windowId, { focused: true }); } } catch (e) { /* never fail the run on focus error */ }
        return { results: [], needs_human: true, error: "Session expired — the site is showing a login page" };
      }
      // Results render asynchronously — wait for the card selector to exist
      // before extracting instead of racing the page build.
      await waitForElement(step.card, 12000);
      let rows = await cmdExtract(step.card, step.fields || {}, 30);
      // Seek loads results asynchronously — the first extract can run while
      // the page still shows the empty-state placeholder. Retry once after
      // an extra beat before accepting 0 rows.
      const realRows = (rs) => rs.filter((r) => (r.title || "").trim() || (r.raw_text || "").length > 60);
      if (realRows(rows).length === 0) {
        await sleep(5000);
        rows = rows.concat(await cmdExtract(step.card, step.fields || {}, 30));
      }
      results.push(...rows);
    }
  }
  return { results };
}

async function cmdDiscoverFlow(baseUrl, query) {
  const u = new URL(baseUrl);
  // ALWAYS work in the agent's own tab — never inspect or reuse the user's
  // current tab. Navigate to the site there.
  await cmdNavigate(baseUrl);
  await sleep(1500);

  // Guest-wall guard: LinkedIn (and similar) may show a login page to the
  // agent tab if the browser isn't signed in — say so precisely instead of
  // the generic "no search box".
  const pageKind = await execOnTab(() => {
    const hasPw = !!document.querySelector("input[type='password']");
    const t = (document.body?.innerText || "").slice(0, 400).toLowerCase();
    return {
      loginWall: hasPw || /sign in|log in|sign up|join linkedin/.test(t),
      url: location.href.slice(0, 120),
    };
  });
  if (pageKind && pageKind.loginWall) {
    throw new Error(
      "The agent tab is showing a sign-in page — click Re-login first, sign in to LinkedIn in the tab that opens, then press Record again"
    );
  }

  const searchSel = await execOnTab(() => {
    const inputs = Array.from(
      document.querySelectorAll("input[type='search'], input[name*='query' i], input[name*='search' i], input[placeholder*='search' i], input[aria-label*='search' i], input[type='text']")
    );
    const visible = inputs.filter((el) => el.offsetWidth || el.offsetHeight);
    const el = visible[0];
    if (!el) return null;
    el.scrollIntoView({ block: "center" });
    if (el.id) return "#" + CSS.escape(el.id);
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return `${el.tagName.toLowerCase()}[type="${el.type}"]`;
  });
  if (!searchSel) throw new Error("No search box found on the page");

  const steps = [
    { action: "navigate", url: baseUrl },
    { action: "fill", selector: searchSel, param: "query" },
    { action: "press", key: "Enter" },
    { action: "wait", seconds: 3 },
  ];

  await cmdFill(searchSel, query);
  await cmdPress("Enter");
  await sleep(3500);

  const cardCandidates = (await execOnTab(() => {
    // Group siblings by signature (tag + first-2-classes) instead of counting
    // same-tagName only — real card lists often mix tags/classes.
    const signature = (el) => {
      const cls =
        el.className && typeof el.className === "string"
          ? el.className.trim().split(/\s+/).slice(0, 2).join(".")
          : "";
      return el.tagName.toLowerCase() + (cls ? "." + cls : "");
    };
    const scored = [];
    for (const el of document.querySelectorAll("article, li, div, section, tr, [role='listitem'], a")) {
      if (!el.querySelector("a") && el.tagName !== "A") continue;
      const textLen = (el.innerText || "").length;
      if (textLen < 60) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < 150 || rect.height < 40) continue;
      let siblings = 1;
      if (el.parentElement) {
        const groups = new Map();
        for (const c of el.parentElement.children) {
          const sig = signature(c);
          groups.set(sig, (groups.get(sig) || 0) + 1);
        }
        siblings = Math.max(...groups.values());
      }
      if (siblings >= 2) {
        let sel = el.tagName.toLowerCase();
        if (el.id && document.querySelectorAll("#" + CSS.escape(el.id)).length === 1) {
          sel += "#" + CSS.escape(el.id);
        } else if (el.className && typeof el.className === "string") {
          const cls = el.className.trim().split(/\s+/)[0];
          if (cls) sel += "." + CSS.escape(cls);
        }
        scored.push({ sel, textLen });
      }
    }
    scored.sort((a, b) => b.textLen - a.textLen);
    return scored.slice(0, 5).map((s) => s.sel);
  })) || [];
  if (cardCandidates.length === 0) {
    throw new Error("Could not find repeated result cards — search for something first, then retry");
  }

  const card = cardCandidates[0];
  const fields = (await execOnTab((cardSel) => {
    const c = document.querySelector(cardSel);
    if (!c) return {};
    const link = c.querySelector("a h1, a h2, a h3, a [class*='title'], a");
    const out = {};
    if (link) {
      const tag = link.tagName.toLowerCase();
      out.title =
        tag +
        (link.className && typeof link.className === "string" && link.className.trim()
          ? "." + link.className.trim().split(/\s+/)[0]
          : "");
    }
    return out;
  }, [card])) || {};

  steps.push({ card, fields });
  return { steps, card, fields, raw: [] };
}

// --- LinkedIn people search (ported from services/linkedin_people.py) ------

// Certification/acronym lines the old location heuristic mistook for
// locations (e.g. "CISA, ITIL Expert, PMP, CEH" under a name).
const CERT_LINE_RE =
  /\b(CISA|CISM|CISSP|ITIL|PMP|PRINCE2|CEH|ACCA|CPA|CFA|CIA|CFP|MBA|B\.?Com|CPAA|CA\s?\(?.?SG\)?)\b/i;

function linkedinSearchUrl(kind, query) {
  // kind: "people" | "jobs" — same URLs the backend builds today.
  const q = encodeURIComponent(query || "");
  return kind === "jobs"
    ? `https://www.linkedin.com/jobs/search/?keywords=${q}`
    : `https://www.linkedin.com/search/results/people/?keywords=${q}`;
}

async function linkedinWallGuard() {
  // Returns an error string when the agent tab is on a login/captcha wall.
  const state = await execOnTab(() => ({
    url: location.href,
    title: document.title || "",
    hasPw: !!document.querySelector("input[type='password']"),
    text: (document.body?.innerText || "").slice(0, 400).toLowerCase(),
  }));
  const url = (state.url || "").toLowerCase();
  if (/authwall|login|checkpoint/.test(url)) {
    return "LinkedIn login wall / session expired";
  }
  if (state.hasPw && /linkedin/.test(url)) {
    return "LinkedIn login wall / session expired";
  }
  if (/captcha|challenge|unusual activity/.test((state.title || "").toLowerCase())) {
    return "LinkedIn presented a CAPTCHA/challenge";
  }
  return null;
}

async function cmdLinkedinPeopleExtract() {
  // Port of _extract_candidates: find the results container (element whose
  // children each hold a /in/ profile link), then parse the same fields.
  const cards =
    (await execOnTab(() => {
      const main = document.querySelector("main") || document.body;
      // Score EVERY candidate container instead of taking the first match:
      // earlier wrapper strips (suggested searches, network rails) can match
      // first in document order with 3+ /in/-link children whose innerText
      // is empty (image-only links) — that yielded 0 candidates silently.
      // The REAL results list has the most in-link children, most of them
      // with visible name text.
      let best = null;
      for (const el of Array.from(main.querySelectorAll("*"))) {
        const kids = Array.from(el.children);
        const inKids = kids.filter((k) => k.querySelector("a[href*='/in/']"));
        if (inKids.length < 3) continue;
        let named = 0;
        let textLen = 0;
        for (const k of inKids) {
          const a = k.querySelector("a[href*='/in/']");
          if ((a.innerText || "").trim().length > 0) named++;
          textLen += (k.innerText || "").length;
        }
        const score = inKids.length * 10000 + named * 100 + Math.min(textLen, 9999);
        if (!best || score > best.score) best = { el, score };
      }
      if (!best) return { error: "no results container found", results: [] };
      const seen = new Set();
      const results = [];
      for (const kid of Array.from(best.el.children)) {
        const nameLink = kid.querySelector("a[href*='/in/']");
        if (!nameLink) continue;
        let href = nameLink.getAttribute("href") || "";
        if (href && seen.has(href)) continue;
        if (href) seen.add(href);
        // Name fallbacks: image-only links have empty innerText.
        let name =
          (nameLink.innerText || "").trim() ||
          (nameLink.getAttribute("aria-label") || "").trim() ||
          (nameLink.querySelector("img")?.getAttribute("alt") || "").trim();
        results.push({
          name,
          href,
          text: (kid.innerText || "").trim(),
        });
      }
      return { results };
    })) || {};
  if (cards.error) return { error: cards.error, candidates: [] };

  const candidates = [];
  for (const item of (cards.results || []).slice(0, 25)) {
    let name = (item.name || "").trim();
    name = name.split("•")[0].trim(); // "Name • 2nd" connection degree
    // Still unnamed after fallbacks (e.g. hidden rail entries) — skip only
    // if there is no profile URL to anchor the row either.
    if (!name && !item.href) continue;
    if (!name) name = "LinkedIn Member";
    const lines = (item.text || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const location =
      lines.slice(1, 6).find(
        (ln) =>
          ln.length < 50 &&
          ln.includes(",") &&
          !CERT_LINE_RE.test(ln) &&
          !ln.startsWith("Current:") &&
          !ln.startsWith("Past:")
      ) || null;
    const headline =
      lines
        .slice(1, 8)
        .find(
          (ln) =>
            ln.length > 15 &&
            !ln.startsWith("Current:") &&
            !ln.startsWith("Past:") &&
            ln !== location &&
            !ln.includes(" is a mutual connection") &&
            !["Connect", "Message", "Follow"].includes(ln)
        ) || null;
    const currentLine = lines.find((ln) => ln.startsWith("Current:"));
    candidates.push({
      id: "li-people-ext-" + Math.abs([...(name + item.href)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7)),
      name,
      headline,
      location,
      summary: (item.text || "").slice(0, 500),
      current_role: currentLine ? currentLine.slice(9) : null,
      skills: [],
      source: "linkedin_people",
      source_url: item.href || "",
      experience: (item.text || "").slice(0, 800),
    });
  }
  return { candidates };
}

async function cmdLinkedinProfileDetail(profileUrl) {
  // Port of _extract_profile_detail: sections keyed by h2 heading.
  let full = "";
  if (profileUrl.startsWith("/")) profileUrl = "https://www.linkedin.com" + profileUrl;
  await cmdNavigate(profileUrl);
  await sleep(1200);
  const wall = await linkedinWallGuard();
  if (wall) return { error: wall };
  await execOnTab(() => window.scrollBy(0, 1400));
  await sleep(900);
  const sections =
    (await execOnTab(() => {
      const result = {};
      document.querySelectorAll("section").forEach((sec) => {
        const h2 = sec.querySelector("h2");
        const heading = h2 ? h2.innerText.trim() : "";
        if (heading) result[heading] = (sec.innerText || "").trim().slice(0, 8000);
      });
      return result;
    })) || {};
  const bodyText =
    (await execOnTab(() => (document.body?.innerText || "").slice(0, 20000))) || "";
  let skillsText = sections["Top skills"] || sections["Skills"] || "";
  if (!skillsText) {
    const m = bodyText.match(/Top skills\s*\n(.+)/);
    if (m) skillsText = "Top skills\n" + m[1].split("\n")[0];
  }
  const lines = (skillsText || "").split("\n").map((l) => l.trim()).filter(Boolean);
  const skillLine = lines.slice(1).find((ln) => ln.includes("•"));
  const skills = skillLine
    ? skillLine.split("•").map((s) => s.trim()).filter(Boolean)
    : lines.length > 1
      ? [lines[1]]
      : [];
  return {
    summary: sections["About"] || "",
    skills,
    experience: sections["Experience"] || "",
    education: sections["Education"] || "",
    certifications: sections["Licenses & certifications"] || "",
  };
}

// Pure classifier: maps a raw results-page snapshot to a page state. Kept
// free of DOM access so it can be unit-tested without a browser; the probe
// below collects the raw fields, and this decides what they mean.
// A GENUINELY EMPTY results page (LinkedIn's own "No results found" state for
// a narrow boolean query) must never be mistaken for a throttled/unrendered
// page — the two look identical when the tab is backgrounded (active:false).
function classifyLinkedinResultsPage(s) {
  // s = { low, hasPw, inLinks, hasScaffoldSelector, hasEmptyStateSelector,
  //       bodyLen, url, title }
  const emptyMarkers = [
    "no results found",
    "we couldn't find a match",
    "we couldn't find any",
    "try a new search",
    "no results for",
    "no matching",
    "your search did not match",
  ];
  const genuineEmpty =
    emptyMarkers.some((m) => (s.low || "").includes(m)) || !!s.hasEmptyStateSelector;
  // Scaffold = the search-results chrome actually rendered (filters / result
  // list / a "N results" line). If it rendered and there are no rows, that is
  // "no matches", not a page that never loaded.
  const scaffold = !!s.hasScaffoldSelector && /result/.test((s.low || "").slice(0, 600));
  return {
    loaded: scaffold && !s.hasPw,
    genuineEmpty,
    inLinks: s.inLinks || 0,
    scaffold,
    bodyLen: s.bodyLen || 0,
    url: s.url || "",
    title: s.title || "",
    text: (s.low || "").slice(0, 300),
  };
}

async function linkedinResultsPageState() {
  // Classify the current results page so a legitimately-empty page is never
  // mistaken for throttling. Collect only raw DOM facts in the tab, then
  // classify in background scope (testable).
  const snap =
    (await execOnTab(() => {
      const txt = document.body?.innerText || "";
      const low = txt.slice(0, 6000).toLowerCase();
      return {
        low,
        hasPw: !!document.querySelector("input[type='password']"),
        inLinks: document.querySelectorAll("a[href*='/in/']").length,
        hasScaffoldSelector: !!document.querySelector(
          ".reusable-search__entity-result-list, .search-results-container, li.reusable-search__result-container, .search-results__filters, .artdeco-pill, h1"
        ),
        hasEmptyStateSelector: !!document.querySelector(
          ".artdeco-empty-state, .search-no-results, [data-test-id='no-results'], .reusable-search__entity-result-list--empty"
        ),
        bodyLen: txt.length,
        url: location.href,
        title: document.title || "",
      };
    })) || {};
  return classifyLinkedinResultsPage(snap);
}

async function cmdLinkedinPeoplePlan(params) {
  // Full sourcing plan in ONE command so the backend's single dispatch maps
  // to one atomic extension execution (no interleaved queue state).
  const { queries = [], excludes = [], location = "", enrichBudget = 10 } = params;
  const merged = [];
  const perQuery = [];
  let blocker = null;
  let consecutiveZeroes = 0;
  const pageDiag = [];
  for (const q of queries) {
    // LinkedIn rate-limits rapid successive distinct people searches —
    // live evidence: 1-query plans always succeed, 5-query plans at ~2s
    // pacing reliably trip the soft throttle (empty pages). Pace 6-11s.
    await sleep(6000 + Math.floor(Math.random() * 5000)); // polite pacing
    await cmdNavigate(linkedinSearchUrl("people", q));
    await sleep(1500);
    const wall = await linkedinWallGuard();
    if (wall) {
      blocker = blocker || wall;
      perQuery.push(`${q.slice(0, 30)}…: blocked`);
      continue;
    }
    const { candidates, error } = await cmdLinkedinPeopleExtract();
    let n = error ? 0 : candidates.length;
    // Extract can zero out on a perfectly good results page (live evidence:
    // complex multi-OR queries land on layouts the container heuristic
    // scores 0 while /in/ links exist — harvest found 2 on the SAME page).
    // So when extract is empty, harvest THIS page immediately before
    // navigating away, instead of assuming throttling.
    let harvested = [];
    if (n === 0) {
      harvested = await execOnTab(() => {
        const out = [];
        const seen = new Set();
        for (const a of document.querySelectorAll("a[href*='/in/']")) {
          const href = a.getAttribute("href") || "";
          if (!href || seen.has(href)) continue;
          const name =
            (a.innerText || "").trim() ||
            (a.getAttribute("aria-label") || "").trim() ||
            (a.querySelector("img")?.getAttribute("alt") || "").trim();
          const card = a.closest("li, div");
          const text = (card?.innerText || "").trim();
          if (!name && !text) continue;
          seen.add(href);
          out.push({ name, href, text: text.slice(0, 800) });
        }
        return out.slice(0, 25);
      }) || [];
    }
    // Only when the page truly has NO profile links at all treat it as a
    // soft throttle: back off, retry once.
    if (n === 0 && harvested.length === 0) {
      await sleep(4000 + Math.floor(Math.random() * 3000));
      await cmdNavigate(linkedinSearchUrl("people", q));
      await sleep(2500);
      const wall2 = await linkedinWallGuard();
      if (!wall2) {
        const retry = await cmdLinkedinPeopleExtract();
        n = retry.error ? 0 : retry.candidates.length;
        if (n > 0) candidates.push(...retry.candidates);
      }
    }
    for (const f of harvested) {
      let name = (f.name || "").trim().split("•")[0].trim();
      if (!name) name = "LinkedIn Member";
      candidates.push({
        id: "li-people-ext-" + Math.abs([...(name + f.href)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7)),
        name,
        headline: null,
        location: null,
        summary: (f.text || "").slice(0, 500),
        current_role: null,
        skills: [],
        source: "linkedin_people",
        source_url: f.href,
        _hit_count: 1,
      });
    }
    n = candidates.length;
    if (n === 0) {
      // Zero rows after extract + harvest + retry. Probe the page to tell a
      // GENUINELY empty result (LinkedIn's own "No results found" state for a
      // narrow query) from a throttled/unrendered page — they are
      // indistinguishable when the results tab is backgrounded.
      const state = await linkedinResultsPageState();
      if (state.genuineEmpty) {
        // Legitimate no-match: not a strike; keep planning the other queries.
        consecutiveZeroes = 0;
        perQuery.push(`${q.slice(0, 30)}${q.length > 30 ? "…" : ""}: 0 (no results)`);
        merged.push(...candidates);
        continue;
      }
      consecutiveZeroes += 1;
      perQuery.push(`${q.slice(0, 30)}${q.length > 30 ? "…" : ""}: ${n}`);
      merged.push(...candidates);
      pageDiag.push({
        q: q.slice(0, 40),
        url: (() => {
          try {
            const u = new URL(state.url);
            return u.host + u.pathname;
          } catch {
            return state.url || "";
          }
        })(),
        title: (state.title || "").slice(0, 80),
        inLinks: state.inLinks,
        scaffold: state.scaffold,
        bodyLen: state.bodyLen,
      });
    } else {
      consecutiveZeroes = 0;
      perQuery.push(`${q.slice(0, 30)}${q.length > 30 ? "…" : ""}: ${n}`);
      merged.push(...candidates);
    }
    // Several blank (non-empty-state) pages in a row = throttled/unrendered.
    // Stop burning the remaining queries; report honestly. Keep "throttl" so
    // the backend (linkedin_people.py) still schedules its 45s retry.
    if (consecutiveZeroes >= 3 && merged.length === 0) {
      blocker =
        "LinkedIn returned blank pages repeatedly (page not loaded / likely throttling) — wait a few minutes and re-run" +
        (pageDiag.length ? ` [${JSON.stringify(pageDiag).slice(0, 500)}]` : "");
      break;
    }
  }
  // Safety net: if every query parsed empty, harvest ALL /in/ links on the
  // LAST results page directly (name from link text / aria-label / img alt).
  // The container heuristic should not be able to zero out a real results
  // page — this guarantees rows whenever profile links exist.
  if (!merged.length && !blocker) {
    const fallback = await execOnTab(() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll("a[href*='/in/']")) {
        const href = a.getAttribute("href") || "";
        if (!href || seen.has(href)) continue;
        const name =
          (a.innerText || "").trim() ||
          (a.getAttribute("aria-label") || "").trim() ||
          (a.querySelector("img")?.getAttribute("alt") || "").trim();
        const card = a.closest("li, div");
        const text = (card?.innerText || "").trim();
        if (!name && !text) continue;
        seen.add(href);
        out.push({ name, href, text: text.slice(0, 800) });
      }
      return out.slice(0, 25);
    });
    for (const f of fallback || []) {
      let name = (f.name || "").trim().split("•")[0].trim();
      if (!name) name = "LinkedIn Member";
      merged.push({
        id: "li-people-ext-" + Math.abs([...(name + f.href)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7)),
        name,
        headline: null,
        location: null,
        summary: (f.text || "").slice(0, 500),
        current_role: null,
        skills: [],
        source: "linkedin_people",
        source_url: f.href,
        experience: (f.text || "").slice(0, 800),
      });
    }
    perQuery.push(`fallback-harvest: ${merged.length}`);
  }
  // Dedupe by normalized profile URL (host → www, strip query/#/trailing /).
  const seen = new Map();
  for (const c of merged) {
    let u = c.source_url || "";
    if (u.startsWith("/")) u = "https://www.linkedin.com" + u;
    u = u.replace("://linkedin.com", "://www.linkedin.com").split("?")[0].split("#")[0].replace(/\/+$/, "");
    if (!u) continue;
    if (seen.has(u)) seen.get(u)._hit_count += 1;
    else {
      c._hit_count = 1;
      seen.set(u, c);
    }
  }
  let uniques = Array.from(seen.values());
  // Location post-filter: drop only cards clearly naming another country.
  const target = (location || "").trim().toLowerCase();
  let dropped = 0;
  if (target) {
    uniques = uniques.filter((c) => {
      const loc = (c.location || "").trim().toLowerCase();
      if (loc && !loc.includes(target)) {
        dropped += 1;
        return false;
      }
      return true;
    });
  }
  // Shared enrich budget over the merged top candidates.
  let enriched = 0;
  for (const c of uniques.slice(0, enrichBudget)) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    const detail = await cmdLinkedinProfileDetail(c.source_url);
    if (detail.error) {
      blocker = blocker || detail.error;
      break;
    }
    c.summary = detail.summary || c.summary;
    c.skills = detail.skills?.length ? detail.skills : c.skills;
    c.experience = detail.experience || c.experience;
    c.education = detail.education;
    c.certifications = detail.certifications;
    enriched += 1;
  }
  return {
    raw_results: uniques,
    needs_human: uniques.length === 0 && !!blocker,
    human_reason: blocker,
    plan_detail:
      `Extension plan v2: ${queries.length} queries [${perQuery.join("; ")}] → ${uniques.length} unique (${dropped} location-dropped, ${enriched} enriched)` +
      (pageDiag.length ? ` | pageDiag: ${JSON.stringify(pageDiag).slice(0, 600)}` : ""),
  };
}

// Standalone enrichment: open top-N profile URLs and merge detail sections.
// Used by the backend to top-up unenriched rows after merge (relaxed pass).
async function cmdLinkedinPeopleEnrich(params) {
  const { candidates = [], enrichBudget = 10 } = params;
  const targets = candidates.slice(0, enrichBudget);
  let blocker = null;
  for (const c of targets) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    const detail = await cmdLinkedinProfileDetail(c.source_url);
    if (detail.error) {
      blocker = blocker || detail.error;
      break;
    }
    c.summary = detail.summary || c.summary;
    c.skills = detail.skills?.length ? detail.skills : c.skills;
    c.experience = detail.experience || c.experience;
    c.education = detail.education;
    c.certifications = detail.certifications;
  }
  return { candidates: targets, error: blocker };
}

// --- LinkedIn jobs search (ported from services/linkedin.py) ---------------

async function cmdLinkedinJobsSearch(params) {
  // One search + detail opens, mirroring search_linkedin_jobs' row shape.
  const { query = "", location = "", maxJobs = 25, detailBudget = 5 } = params;
  await sleep(1200 + Math.floor(Math.random() * 1500));
  await cmdNavigate(linkedinSearchUrl("jobs", query));
  await sleep(2500); // jobs list renders slower
  const wall = await linkedinWallGuard();
  if (wall) {
    return { raw_results: [], needs_human: true, human_reason: wall };
  }
  const rows =
    (await execOnTab(() => {
      const cards = document.querySelectorAll("li.scaffold-layout__list-item");
      const out = [];
      for (const c of Array.from(cards)) {
        const titleEl = c.querySelector(".job-card-container__link");
        const title = titleEl ? (titleEl.innerText || "").trim() : "";
        if (!title) continue;
        const company = (c.querySelector(".artdeco-entity-lockup__subtitle")?.innerText || "").trim();
        const metadata = (c.querySelector(".job-card-container__metadata-wrapper")?.innerText || "").trim();
        const footer = (c.querySelector(".job-card-list__footer-wrapper")?.innerText || "").trim();
        const mLines = metadata.split("\n").map((l) => l.trim()).filter(Boolean);
        const fLines = footer.split("\n").map((l) => l.trim()).filter(Boolean);
        out.push({
          title,
          href: (titleEl && titleEl.getAttribute("href")) || "",
          company,
          location: mLines[0] || null,
          salary_text: mLines.find((ln) => ln.includes("SGD") || ln.includes("$") || ln.includes("K")) || null,
          posted_at: fLines[0] || null,
          metadata_footer: footer,
        });
      }
      return out;
    })) || [];
  const seen = new Set();
  const jobs = [];
  for (const r of rows.slice(0, maxJobs)) {
    let href = r.href || "";
    if (href.startsWith("/")) href = "https://www.linkedin.com" + href;
    const key = r.title + "|" + r.company + "|" + href;
    if (seen.has(key)) continue;
    seen.add(key);
    jobs.push({
      id: "li-ext-" + Math.abs([...key].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) | 0, 7)),
      title: r.title,
      company: r.company,
      location: r.location,
      salary_text: r.salary_text,
      description: "",
      source: "linkedin",
      source_url: href,
      posted_at: r.posted_at,
      metadata_footer: r.metadata_footer,
    });
  }
  // Detail opens for the top jobs (description text).
  let opened = 0;
  for (const j of jobs.slice(0, detailBudget)) {
    await sleep(1500 + Math.floor(Math.random() * 1500));
    await cmdNavigate(j.source_url);
    await sleep(1500);
    const w2 = await linkedinWallGuard();
    if (w2) break;
    await execOnTab(() => window.scrollBy(0, 1200));
    await sleep(800);
    const text =
      (await execOnTab(() => {
        const el =
          document.querySelector(".jobs-description-content__text") ||
          document.querySelector(".jobs-box__html-content");
        if (el) return (el.innerText || "").trim().slice(0, 20000);
        for (const e of document.querySelectorAll("*")) {
          const t = (e.innerText || "").trim();
          if (t.startsWith("About the job") && t.length > 200) return t.slice(0, 20000);
        }
        return "";
      })) || "";
    j.description = text;
    if (!j.posted_at) {
      const top =
        (await execOnTab(() =>
          (document.querySelector(".jobs-unified-top-card__content--two-pane, .jobs-unified-top-card")?.innerText || "")
        )) || "";
      const lines = top.split("\n").map((l) => l.trim()).filter(Boolean);
      j.posted_at = lines.find((ln) => /ago|day|week|month/.test(ln)) || j.posted_at;
    }
    opened += 1;
  }
  return {
    raw_results: jobs,
    needs_human: false,
    human_reason: null,
    plan_detail: `Extension jobs: ${query.slice(0, 40)} → ${jobs.length} jobs (${opened} detail-opened)`,
  };
}

// --- manual filter recorder --------------------------------------------------
// Records the user's clicks in the agent tab (filter panels, dropdowns, tabs)
// so a flow can replay them after the keyword search. Two commands:
//   start_record — injects a capture-phase click listener into the agent tab
//   stop_record  — reads the collected events and tears the listener down
// Events live on window.__caRecord in the page itself, so soft navigations
// within an SPA (seek's filter panel re-renders in place) don't lose them.

async function cmdStartRecord(baseUrl) {
  // The recorder must live in the tab the USER will click filters in.
  // After a worker reload agentTabId is null — creating about:blank here
  // (the old behavior) installed the recorder on a hidden empty tab, so
  // every click on the real site was lost ("No clicks were recorded").
  // Instead: reuse the agent tab if it's on the source site, else attach to
  // the user's existing tab on that site, else open a NEW ACTIVE tab there
  // (active — the user must see it to click filters).
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      if (!t || t.url === undefined || !t.url.includes("http")) {
        agentTabId = null;
      } else if (baseUrl) {
        // Reuse path: the tab exists but may be on another site and is almost
        // certainly backgrounded. Navigate it to the source site (only when it
        // isn't already there) and ALWAYS foreground it, so the user can
        // actually click the filters the recorder is meant to capture.
        try {
          const host = new URL(baseUrl).hostname;
          const cur = t.url || "";
          if (!cur.includes(host)) {
            const loadP = waitForComplete(agentTabId);
            await chrome.tabs.update(agentTabId, { url: baseUrl });
            await loadP;
          }
        } catch {
          /* bad baseUrl — leave URL as-is, still activate below */
        }
        await activateTab(agentTabId);
      }
    } catch {
      agentTabId = null;
    }
  }
  if (agentTabId === null && baseUrl) {
    const host = new URL(baseUrl).hostname;
    const tabs = await chrome.tabs.query({ url: `*://*.${host}/*` });
    if (tabs.length > 0) {
      agentTabId = tabs[0].id;
      await chrome.tabs.update(agentTabId, { active: true });
    } else {
      const tab = await chrome.tabs.create({ active: true, url: baseUrl });
      agentTabId = tab.id;
      await waitForComplete(agentTabId);
    }
  }
  if (agentTabId === null) {
    await ensureTab(baseUrl || "about:blank");
  }
  // Whatever path selected the tab, guarantee the recorder runs in the tab the
  // USER sees. The recorder injects into agentTabId, but the user clicks in
  // whatever tab is FOREGROUNDED; if those differ (e.g. a backgrounded user
  // tab picked by the query, or a stale agent tab) every click lands in a tab
  // with no recorder and Stop reports "no clicks recorded" / a 422. Forcing
  // agentTabId active (and its window focused) closes that gap.
  if (agentTabId !== null) {
    await activateTab(agentTabId);
  }
  // A recording is active across navigations: flag it so the tabs.onUpdated
  // hook re-injects the capture listener after a hard reload (the recorder
  // lives in the page and dies when the document is replaced).
  recordingActive = true;
  try {
    await chrome.storage.session.set({ [REC_ACTIVE_KEY]: true });
  } catch {
    /* session storage is best-effort */
  }
  // Seed from any events already captured in THIS recording (a reload of the
  // recording session must continue the same event stream, not restart it).
  let seed = [];
  try {
    const stored = await chrome.storage.session.get([REC_EVENTS_KEY]);
    seed = stored[REC_EVENTS_KEY] || [];
  } catch {
    /* best-effort */
  }
  await injectRecorder(seed);
  return { ok: true, recording: true };
}

// injectRecorder — (re)install the click/fill/press capture listeners in the
// agent tab, seeded with the events captured so far. Idempotent: a document
// that already has the recorder is left alone (onUpdated fires "complete"
// for background tabs and SPA route changes). Never throws into the caller.
async function injectRecorder(seed = []) {
  // MAIN world: capture listeners + the in-page event buffer.
  await execOnTab(_mainWorldRecorder, [seed]).catch(() => {});
  // ISOLATED world: bridges captured events to chrome.storage.session so a
  // hard navigation (which destroys the MAIN-world recorder) still keeps the
  // events the user already produced.
  await execOnTab(_isoRecorderBridge, [], "ISOLATED").catch(() => {});
}

// Main-world recorder installer. MUST be self-contained (serialized into the
// page): no closures over module state. Builds a CSS selector for an element,
// captures click + input change + Enter, and mirrors every event to the
// ISOLATED world via a DOM CustomEvent for session persistence. Typed text is
// NEVER recorded — the backend templatizes a text-input fill to param:query.
function _mainWorldRecorder(seed) {
  const mergeSeed = () => {
    if (!seed || !seed.length) return;
    const have = new Set(
      window.__caRecord.events.map((e) => `${e.ts}|${e.selector}`)
    );
    for (const e of seed) {
      if (!have.has(`${e.ts}|${e.selector}`)) window.__caRecord.events.push(e);
    }
  };
  if (window.__caRecord) {
    mergeSeed();
    return { already: true };
  }
  window.__caRecord = { events: (seed || []).slice(), capture: null };

  // Build a CSS selector for an element: prefer unique id, then
  // data-testid/aria-label, then a short tag+class path (max 4 levels).
  function buildSelector(el) {
    if (!(el instanceof Element)) return null;
    if (el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1) {
      return `#${CSS.escape(el.id)}`;
    }
    const dt = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (dt && document.querySelectorAll(`[data-testid="${dt}"]`).length === 1) {
      return `[data-testid="${dt}"]`;
    }
    const aria = el.getAttribute("aria-label");
    if (aria && document.querySelectorAll(`[aria-label="${aria}"]`).length === 1) {
      return `[aria-label="${aria}"]`;
    }
    // name+type for form controls
    if (el.name && document.getElementsByName(el.name).length === 1) {
      return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    }
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur instanceof Element && depth < 4) {
      let part = cur.tagName.toLowerCase();
      if (cur.id && document.querySelectorAll(`#${CSS.escape(cur.id)}`).length === 1) {
        parts.unshift(`#${CSS.escape(cur.id)}`);
        break;
      }
      if (cur.className && typeof cur.className === "string" && cur.className.trim()) {
        const cls = cur.className.trim().split(/\s+/).slice(0, 2);
        const scoped = cls.map((c) => `.${CSS.escape(c)}`).join("");
        part += scoped;
      }
      // nth-of-type disambiguation among siblings
      const parent = cur.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (sameTag.length > 1) {
          part += `:nth-of-type(${sameTag.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(part);
      cur = cur.parentElement;
      depth += 1;
    }
    return parts.join(" > ");
  }

  const push = (event) => {
    try {
      window.__caRecord.events.push(event);
      // Mirror to the ISOLATED world for chrome.storage.session persistence.
      document.dispatchEvent(
        new CustomEvent("__caRecordEvent", { detail: event })
      );
    } catch {
      /* never let persistence break the page */
    }
  };

  const capture = (ev) => {
    try {
      // Only left clicks on real elements; ignore the recorder's own UI.
      if (ev.button !== 0) return;
      const el = ev.target;
      if (!(el instanceof Element)) return;
      if (el.closest("[data-ca-record-ignore]")) return;
      const label = (
        el.getAttribute("aria-label") ||
        el.getAttribute("title") ||
        (el.textContent || "").trim().slice(0, 60) ||
        el.tagName.toLowerCase()
      );
      push({
        action: "click",
        selector: buildSelector(el),
        text: label,
        ts: Date.now(),
      });
      // Visual feedback flash so the user sees what's captured.
      const prev = el.style.outline;
      el.style.outline = "2px solid #2e9e5b";
      setTimeout(() => {
        el.style.outline = prev;
      }, 400);
    } catch {
      /* never let the recorder break the page */
    }
  };

  // Typed text is deliberately NOT stored: record the fill (so the step
  // replays as the search term) but never the literal characters — the
  // backend converts an input fill to {"param": "query"} on stop.
  const captureChange = (ev) => {
    try {
      const el = ev.target;
      if (!(el instanceof Element)) return;
      if (!/^(input|textarea)$/i.test(el.tagName)) return;
      if (el.closest("[data-ca-record-ignore]")) return;
      if ((el.getAttribute("type") || "").toLowerCase() === "password") return;
      push({ action: "fill", selector: buildSelector(el), ts: Date.now() });
    } catch {
      /* best-effort */
    }
  };

  const captureKeydown = (ev) => {
    try {
      if (ev.key !== "Enter") return;
      const el = ev.target;
      if (!(el instanceof Element)) return;
      if (!/^(input|textarea)$/i.test(el.tagName)) return;
      push({ action: "press", key: "Enter", selector: buildSelector(el), ts: Date.now() });
    } catch {
      /* best-effort */
    }
  };

  window.__caRecord.capture = capture;
  window.__caRecord.captureChange = captureChange;
  window.__caRecord.captureKeydown = captureKeydown;
  // capture phase so dropdown option handlers (which may unmount the element
  // on click) still get recorded.
  document.addEventListener("click", capture, true);
  document.addEventListener("change", captureChange, true);
  document.addEventListener("keydown", captureKeydown, true);
  return { ok: true };
}

// Isolated-world bridge: persist MAIN-world recorder events to
// chrome.storage.session so a hard navigation mid-recording keeps them.
function _isoRecorderBridge() {
  if (window.__caRecordBridge) return { already: true };
  window.__caRecordBridge = true;
  document.addEventListener("__caRecordEvent", (ev) => {
    try {
      const event = ev && ev.detail;
      if (!event) return;
      chrome.storage.session.get(["caRecordEvents"]).then((s) => {
        const arr = s.caRecordEvents || [];
        arr.push(event);
        chrome.storage.session.set({ caRecordEvents: arr });
      }).catch(() => {});
    } catch {
      /* fire-and-forget: recording must never throw */
    }
  });
  return { ok: true };
}

// page_state — diagnostic snapshot of the current tab so a failed
// detection can be explained (login wall vs. empty results vs. not loaded).
async function cmdPageState(selectors) {
  return execOnTab(() => {
    const text = (document.body?.innerText || "").slice(0, 2000);
    const counts = {};
    for (const sel of (selectors || []).slice(0, 8)) {
      try {
        counts[sel] = document.querySelectorAll(sel).length;
      } catch {
        counts[sel] = -1;
      }
    }
    return {
      url: location.href.slice(0, 200),
      title: document.title.slice(0, 100),
      bodyChars: (document.body?.innerText || "").length,
      bodyHead: text.slice(0, 300),
      loginHint: /sign in|log in|password|authwall|verify/i.test(text),
      counts,
    };
  });
}

// find_result_card — detect the repeating result-row container on the
// CURRENT page so a rotten extract selector can be re-synthesized during
// re-record (seek rotates obfuscated classes; a stored card selector dies
// silently). Strategy: group elements by tag+first-class signature, find
// the signature with the most siblings (3+) whose rows carry substantial
// multi-line text and at least one link — that's the results list.
async function cmdFindResultCard() {
  return execOnTab(() => {
    const groups = new Map();
    for (const el of document.querySelectorAll("body *")) {
      if (el.closest("[data-ca-record-ignore]")) continue;
      const sig =
        el.tagName.toLowerCase() +
        (typeof el.className === "string" && el.className.trim()
          ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".")
          : "");
      let g = groups.get(sig);
      if (!g) {
        g = [];
        groups.set(sig, g);
      }
      g.push(el);
    }
    // Name-shaped first line: 2+ capitalized words, no digits — a person's
    // name rather than a facet label ("Employment Status") or a date.
    const nameShaped = (s) =>
      !!s &&
      s.length >= 5 &&
      !/\d/.test(s) &&
      /^[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’.-]+)+$/.test(s.trim());
    const profileHref = (h) => /\/(candidate|profile|talent|\/p\/)/i.test(h || "");
    const cardSig = /candidate|profile|card|result/i;
    let best = null;
    for (const [sig, els] of groups) {
      if (els.length < 3 || els.length > 60) continue;
      let good = 0;
      let linked = 0;
      let bearing = 0; // rows that carry a profile link or a name-shaped line
      for (const el of els) {
        const text = (el.innerText || "").trim();
        if (text.length < 80 || !text.includes("\n")) continue;
        // Text-rich multi-line row. Good if it has a link OR looks like a
        // result card: card-ish signature, data-testid, or very long text.
        // SEEK talent-search candidate cards have NO links (name is plain
        // text), so requiring a[href] here made detection always fail.
        const hasLink = !!el.querySelector("a[href]");
        if (hasLink) linked += 1;
        const cardish =
          /card|result|profile|candidate/i.test(sig) ||
          el.hasAttribute("data-testid") ||
          text.length > 200;
        if (!hasLink && !cardish) continue;
        good += 1;
        // Candidate/profile-bearing evidence: a profile-ish anchor, OR the
        // first substantial text line looking like a name.
        const hasProfileLink = Array.from(el.querySelectorAll("a[href]")).some((a) =>
          profileHref(a.getAttribute("href") || a.href)
        );
        const firstLine = (text.split("\n").map((l) => l.trim()).find((l) => l.length >= 3) || "");
        if (hasProfileLink || nameShaped(firstLine)) bearing += 1;
      }
      if (good < 3 || good < els.length * 0.5) continue;
      // Tighten acceptance: the signature must have a majority of rows that
      // are candidate/profile-bearing, OR its class signature is card-ish.
      if (bearing < good * 0.5 && !cardSig.test(sig)) continue;
      const score =
        good +
        linked * 0.1 +
        (/card/i.test(sig) ? 0.5 : 0) +
        (bearing > 0 ? 0.3 : 0);
      if (!best || score > best.score)
        best = { sig, good, score, sample: els[0] };
    }
    if (!best) return { found: false };
    // Prefer a name-bearing title selector from the winning row: the first
    // profile-ish anchor, else the element whose text is name-shaped. Kept
    // alongside `card` for backward compat.
    const sample = best.sample;
    let titleSel = "";
    const profAnchor = Array.from(sample.querySelectorAll("a[href]")).find((a) =>
      profileHref(a.getAttribute("href") || a.href)
    );
    if (profAnchor) {
      const p = profAnchor.parentElement;
      titleSel = p && p.tagName.toLowerCase() === "a" ? "a" : "a[href]";
    } else {
      for (const el of sample.querySelectorAll("h1, h2, h3, h4, strong, b, span, div, p")) {
        const t = (el.innerText || "").trim();
        if (nameShaped(t) && el.children.length === 0) {
          titleSel = `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/)[0] : ""}`;
          break;
        }
      }
    }
    // Build a selector for the sample row: parent-id/#app prefix + signature.
    const parent = best.sample.parentElement;
    let prefix = "";
    if (parent) {
      let cur = parent;
      const chain = [];
      while (cur && cur instanceof Element && chain.length < 3) {
        if (cur.id && document.querySelectorAll(`#${CSS.escape(cur.id)}`).length === 1) {
          chain.unshift(`#${CSS.escape(cur.id)}`);
          break;
        }
        chain.unshift(cur.tagName.toLowerCase());
        cur = cur.parentElement;
      }
      prefix = chain.length > 1 ? chain.join(" > ") + " > " : "";
    }
    return { found: true, card: prefix + best.sig, count: best.good, title: titleSel || undefined };
  });
}

// Nav-noise pruning for recorded flows. The recorder captures EVERY left
// click by design (extension/background.js `capture()`), so incidental nav
// clicks ("Malaysia Jobs", "Chats", repeated "Talent search") become steps
// that replay as dead weight. This is a small deterministic denylist + a
// navbar heuristic + same-click dedupe — deliberately NOT semantic NLP.
const NAV_NOISE_TEXT = new Set([
  "malaysia jobs",
  "chats",
  "saved searches",
  "notifications",
  "profile",
  "logout",
  "sign out",
  "home",
  "menu",
]);

function isNavNoise(step) {
  if (!step || step.action !== "click") return false;
  const text = String(step.text || "").trim().toLowerCase();
  const selector = String(step.selector || "");
  if (NAV_NOISE_TEXT.has(text)) return true;
  // Navbar heuristic: a click whose selector chain sits inside a top navbar is
  // incidental UNLESS it is the site's search/talent entry (the search intent).
  const inNavbar =
    /navbar-container|navbar-nav/i.test(selector) ||
    /(^|\s|>)nav\b/i.test(selector.split(">").pop() || "");
  if (inNavbar && !/search|talent/i.test(text)) return true;
  return false;
}

// Drop incidental nav clicks and collapse repeated identical (selector,text)
// clicks to their LAST occurrence (the final, effective click stays in order).
function pruneNavNoise(steps) {
  const kept = [];
  for (const s of steps) {
    if (s && s.action === "click" && !isNavNoise(s)) kept.push(s);
    else if (!s || s.action !== "click") kept.push(s);
  }
  // Collapse duplicates keeping the LAST: walk backwards, keep first seen.
  const seen = new Set();
  const out = [];
  for (let i = kept.length - 1; i >= 0; i--) {
    const s = kept[i];
    if (s && s.action === "click") {
      const key = `${s.selector}\u0000${s.text || ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
    }
    out.unshift(s);
  }
  return out;
}

async function cmdStopRecord() {
  const events =
    (await execOnTab(() => {
      const state = window.__caRecord;
      if (!state) return null;
      const out = state.events.slice();
      if (state.capture) document.removeEventListener("click", state.capture, true);
      if (state.captureChange)
        document.removeEventListener("change", state.captureChange, true);
      if (state.captureKeydown)
        document.removeEventListener("keydown", state.captureKeydown, true);
      delete window.__caRecord;
      return out;
    }).catch(() => null)) || [];
  // Merge the storage mirror: a hard navigation destroyed the in-page buffer,
  // so the events from before the reload live ONLY in chrome.storage.session.
  let stored = [];
  try {
    const s = await chrome.storage.session.get([REC_EVENTS_KEY]);
    stored = s[REC_EVENTS_KEY] || [];
  } catch {
    /* best-effort */
  }
  const merged = [];
  const seen = new Set();
  for (const e of [...stored, ...events]) {
    if (!e || typeof e !== "object") continue;
    const key = `${e.ts}|${e.selector}|${e.action}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(e);
  }
  merged.sort((a, b) => (a.ts || 0) - (b.ts || 0));
  // Keep events with a selector (a press without a selector still carries the
  // key and is replayable; a fill/click without a selector is not).
  const cleaned = [];
  for (const e of merged) {
    const action = e.action || "click";
    if (action !== "press" && !e.selector) continue;
    const last = cleaned[cleaned.length - 1];
    // Collapse rapid duplicate clicks (double-click records twice).
    if (
      last &&
      action === "click" &&
      last.selector === e.selector &&
      e.ts - last.ts < 400
    )
      continue;
    if (action === "fill") {
      cleaned.push({ action: "fill", selector: e.selector, ts: e.ts });
    } else if (action === "press") {
      cleaned.push({ action: "press", key: e.key || "Enter", selector: e.selector, ts: e.ts });
    } else {
      cleaned.push({ action: "click", selector: e.selector, text: e.text, ts: e.ts });
    }
  }
  // Recording is over — clear both storage keys so a later recording starts clean.
  recordingActive = false;
  try {
    await chrome.storage.session.remove([REC_EVENTS_KEY, REC_ACTIVE_KEY]);
  } catch {
    /* best-effort */
  }
  // Prune incidental nav clicks (capture-all by design) then dedupe identical
  // clicks keeping the last. Terminal/nav/wait/fill/press steps untouched.
  const pruned = pruneNavNoise(cleaned);
  return { ok: true, events: pruned, count: pruned.length };
}

// --- dispatch -------------------------------------------------------------

async function executeCommand(cmd) {
  const { action, params = {} } = cmd;
  switch (action) {
    case "navigate": return cmdNavigate(params.url, { activate: params.activate || false });
    case "fill": return cmdFill(params.selector, params.text);
    case "click": return cmdClick(params.selector);
    case "press": return cmdPress(params.key || "Enter");
    case "extract": return cmdExtract(params.card, params.fields, params.maxItems);
    case "run_flow": return cmdRunFlow(params.baseUrl, params.query, params.steps);
    case "discover_flow": return cmdDiscoverFlow(params.baseUrl, params.query, params.flowType);
    case "get_cookies": return cmdGetCookies(params.url);
    case "clear_cookies": return cmdClearCookies(params.url);
    case "start_record": return cmdStartRecord(params.baseUrl);
    case "stop_record": return cmdStopRecord();
    case "page_state": return cmdPageState(params.selectors);
    case "find_result_card": return cmdFindResultCard();
    case "linkedin_people_plan": return cmdLinkedinPeoplePlan(params);
    case "linkedin_people_enrich": return cmdLinkedinPeopleEnrich(params);
    case "linkedin_jobs_search": return cmdLinkedinJobsSearch(params);
    default: throw new Error(`Unknown action: ${action}`);
  }
}

// --- polling loop ----------------------------------------------------------

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollOnce() {
  // Even while a command executes (busy), KEEP POLLING: the fetch itself is
  // the server's liveness signal, and a multi-minute LinkedIn plan used to
  // starve /agent/poll for >40s — the backend then judged the agent
  // offline and the NEXT queued leg fell back to server Playwright with a
  // stale cookie blob ("Session expired" pause). Executing a command and
  // polling are independent fetches; only skip fetching a SECOND command
  // while one is running.
  // Watchdog: if a command runs far beyond every server timeout, its
  // promise leaked (tab closed mid-injection, hung injection) and busy
  // would be stuck true FOREVER — every later command instantly rejected
  // as "agent busy". Force-clear so the agent self-heals.
  if (busy && currentCmdStartedAt && Date.now() - currentCmdStartedAt > COMMAND_WATCHDOG_MS) {
    console.warn("[ca] watchdog: force-clearing stuck busy after", currentCmdAction, Date.now() - currentCmdStartedAt, "ms");
    busy = false;
    currentCmdStartedAt = 0;
  }
  if (!AGENT_ENABLED) {
    // Paused from the popup — never take a job. loop() reschedules us, so
    // re-enabling resumes within one poll interval (or immediately via the
    // set-enabled message).
    setBadge(false);
    return;
  }
  const res = await fetch(`${API_BASE}/api/v1/agent/poll?boot=${encodeURIComponent(BOOT_ID)}`);
  if (!res.ok) {
    setBadge(false);
    return;
  }
  const data = await res.json();
  if (data.command && !busy) {
    const { id, action, params } = data.command;
    setBadge(true);
    busy = true;
    currentCmdStartedAt = Date.now();
    currentCmdAction = action;
    executeCommand({ action, params })
      .then((result) => postResult(id, true, result, null))
      .catch((e) => postResult(id, false, null, String((e && e.message) || e)))
      .finally(() => {
        busy = false;
        currentCmdStartedAt = 0;
      });
  } else if (data.command && busy) {
    // One command at a time — drop the extra command, it will time out
    // server-side; poll keeps flowing so liveness stays fresh.
    await postResult(data.command.id, false, null, "agent busy executing another command");
  } else {
    setBadge(true);
  }
}

async function postResult(id, ok, data, error) {
  try {
    await fetch(`${API_BASE}/api/v1/agent/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, ok, data, error }),
    });
  } catch {
    /* server will time the command out; nothing else to do */
  }
}

async function loop() {
  await loadConfig();
  while (true) {
    await pollOnce();
    await sleep(POLL_INTERVAL_MS);
  }
}

// Guard against multiple concurrent loops (alarm + startup + install can
// each kick one): only one while-loop runs the poll cycle at a time.
let looping = false;
async function loopGuarded() {
  if (looping) return;
  looping = true;
  try {
    await loop();
  } finally {
    looping = false;
  }
}

// Popup "Save & connect" restarts the loop so a changed apiBase applies now
// instead of waiting for the next alarm wake.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "restart-loop") {
    looping = false; // allow a fresh loop with the new config
    loopGuarded();
  }
  if (msg && msg.type === "set-enabled") {
    AGENT_ENABLED = msg.enabled !== false;
    setBadge(AGENT_ENABLED);
    if (AGENT_ENABLED) {
      // Resume immediately rather than waiting for the next poll tick.
      looping = false;
      loopGuarded();
    } else {
      // Tell the backend we're going down so /status flips OFF now instead of
      // after the liveness window. Fire-and-forget — never block the toggle.
      fetch(`${API_BASE}/api/v1/agent/disconnect`, { method: "POST" }).catch(() => {});
    }
  }
});

// Manual recording survives hard navigation: when a page finishes loading
// while a recording is active, re-inject the recorder seeded with the events
// already persisted to session storage. The module flag is lost on worker
// reload, so rehydrate it from chrome.storage.session too. Idempotent — the
// in-page installer skips a document that already has the recorder.
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  try {
    if (info.status !== "complete") return;
    if (tabId !== agentTabId) return;
    if (!recordingActive) {
      const s = await chrome.storage.session.get([REC_ACTIVE_KEY]).catch(() => ({}));
      recordingActive = !!(s && s[REC_ACTIVE_KEY]);
    }
    if (!recordingActive) return;
    const s = await chrome.storage.session.get([REC_EVENTS_KEY]).catch(() => ({}));
    const seed = (s && s[REC_EVENTS_KEY]) || [];
    await injectRecorder(seed);
  } catch {
    /* re-injection is best-effort — never disturb the recording or the user */
  }
});

// A persistent while-loop in the service worker keeps it alive while active,
// and every fetch wakes it if suspended. Chrome suspends idle MV3 workers
// after ~30s regardless of pending work, killing the loop — so a
// chrome.alarms heartbeat (minimum period 30s, but reliable) re-kicks the
// loop whenever the worker is woken. Every fetch in pollOnce() itself wakes
// the worker too when a command arrives mid-suspension isn't possible; the
// alarm bounds the worst-case reconnect latency to ~30s.
const HEARTBEAT_ALARM = "career-agent-heartbeat";

chrome.runtime.onStartup.addListener(loopGuarded);
chrome.runtime.onInstalled.addListener(loopGuarded);

// The ISOLATED-world recorder bridge (a content-script context) needs to
// write recording events to chrome.storage.session — that API is gated to
// trusted contexts by default, so open it to content scripts. Idempotent.
try {
  chrome.storage.session.setAccessLevel({
    accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS",
  });
} catch {
  /* older Chrome — the recorder still works in-page, just not across reloads */
}

chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT_ALARM) {
    loopGuarded();
  }
});

loopGuarded();
