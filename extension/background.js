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
let loopTimer = null;
// The tab the agent opened for its own work — all fill/click/extract target
// THIS tab, never whatever the user happens to have focused.
let agentTabId = null;

async function loadConfig() {
  const stored = await chrome.storage.local.get(["apiBase"]);
  API_BASE = (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#2e9e5b" : "#999" });
}

// --- command execution (unchanged semantics) ------------------------------

async function ensureTab(url) {
  // Always open agent navigation in a NEW background tab — never hijack the
  // user's current tab (it may be the app itself, or anything else).
  // Reuse the existing agent tab if it's still open (one workspace per agent).
  if (agentTabId !== null) {
    try {
      const t = await chrome.tabs.get(agentTabId);
      if (t && t.id !== undefined) {
        await chrome.tabs.update(agentTabId, { url });
        await waitForComplete(agentTabId);
        return agentTabId;
      }
    } catch {
      agentTabId = null; // tab was closed — fall through and create a new one
    }
  }
  const tab = await chrome.tabs.create({ active: false, url });
  agentTabId = tab.id;
  await waitForComplete(tab.id);
  return tab.id;
}

function waitForComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("Page load timed out")), timeoutMs);
    chrome.tabs.onUpdated.addListener(function listener(id, info) {
      if (id === tabId && info.status === "complete") {
        clearTimeout(t);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });
  });
}

// NOTE: there is deliberately no "active tab" concept anywhere in this agent.
// All navigation and script execution happens in the agent-owned tab.

async function execOnTab(fn, args = []) {
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
    world: "MAIN",
    func: fn,
    args,
  });
  return res && res[0] && res[0].result;
}

async function cmdNavigate(url) {
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  await ensureTab(url);
  await sleep(2500); // SPA render beat
  return { url };
}

async function cmdFill(selector, text) {
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
  const r = await execOnTab((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { ok: false, error: "Element not found: " + sel };
    el.scrollIntoView({ block: "center" });
    el.click();
    return { ok: true };
  }, [selector]);
  if (!r || !r.ok) throw new Error((r && r.error) || "click failed");
  await sleep(1500);
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
  await sleep(2000);
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
        out.push({
          title: pick(fieldMap && fieldMap.title),
          company: pick(fieldMap && fieldMap.company),
          location: pick(fieldMap && fieldMap.location),
          summary: pick(fieldMap && fieldMap.summary),
          url: (c.querySelector("a") && c.querySelector("a").href) || "",
          raw_text: (c.innerText || "").slice(0, 500),
        });
      }
      return out;
    }, [cardSelector, fields || {}, maxItems || 30])) || []
  );
}

async function cmdGetCookies(url) {
  const u = new URL(url);
  const cookies = await chrome.cookies.getAll({ domain: u.hostname });
  return cookies.map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path,
    expires: c.expirationDate || -1,
    httpOnly: c.httpOnly,
    secure: c.secure,
    sameSite: c.sameSite === "unspecified" ? "Lax" : c.sameSite,
  }));
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
        return { results: [], needs_human: true, error: "Session expired — the site is showing a login page" };
      }
      results.push(...(await cmdExtract(step.card, step.fields || {}, 30)));
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
    const scored = [];
    for (const el of document.querySelectorAll("article, li, div, section")) {
      if (!el.querySelector("a")) continue;
      const textLen = (el.innerText || "").length;
      if (textLen < 60) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < 150 || rect.height < 40) continue;
      const siblings = el.parentElement
        ? Array.from(el.parentElement.children).filter((c) => c.tagName === el.tagName).length
        : 1;
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

// --- dispatch -------------------------------------------------------------

async function executeCommand(cmd) {
  const { action, params = {} } = cmd;
  switch (action) {
    case "navigate": return cmdNavigate(params.url);
    case "fill": return cmdFill(params.selector, params.text);
    case "click": return cmdClick(params.selector);
    case "press": return cmdPress(params.key || "Enter");
    case "extract": return cmdExtract(params.card, params.fields, params.maxItems);
    case "run_flow": return cmdRunFlow(params.baseUrl, params.query, params.steps);
    case "discover_flow": return cmdDiscoverFlow(params.baseUrl, params.query, params.flowType);
    case "get_cookies": return cmdGetCookies(params.url);
    default: throw new Error(`Unknown action: ${action}`);
  }
}

// --- polling loop ----------------------------------------------------------

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollOnce() {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch(`${API_BASE}/api/v1/agent/poll`);
    if (!res.ok) {
      setBadge(false);
      return;
    }
    const data = await res.json();
    if (data.command) {
      const { id, action, params } = data.command;
      setBadge(true);
      try {
        const result = await executeCommand({ action, params });
        await postResult(id, true, result, null);
      } catch (e) {
        await postResult(id, false, null, String((e && e.message) || e));
      }
    } else {
      // poll itself proves liveness server-side; badge reflects that.
      setBadge(true);
    }
  } catch (e) {
    // API unreachable — expected when backend is down.
    setBadge(false);
  } finally {
    busy = false;
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

// A persistent while-loop in the service worker keeps it alive while active,
// and every fetch wakes it if suspended. Also re-kick on browser events so a
// suspended worker resumes promptly.
chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onInstalled.addListener(loop);
loop();
