/**
 * Career Agent — background service worker.
 *
 * Connects OUTBOUND to the Career Agent API (no inbound ports, no tunnels)
 * and executes browser commands: navigate / fill / click / press / extract /
 * run_flow. Works in the user's real browser, so sites see a genuine client.
 */

const DEFAULT_API = "http://localhost:8000";
let ws = null;
let reconnectTimer = null;
let API_BASE = DEFAULT_API;

// --- config (set once from the popup) ------------------------------------

async function loadConfig() {
  const stored = await chrome.storage.local.get(["apiBase"]);
  API_BASE = (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
}

// --- websocket link ------------------------------------------------------

function connect() {
  loadConfig().then(() => {
    const wsUrl =
      API_BASE.replace(/^http:\/\//, "ws://").replace(/^https:\/\//, "wss://") +
      "/api/v1/agent/ws";
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      console.warn("[career-agent] WS construct failed:", e);
      scheduleReconnect();
      return;
    }
    ws.onerror = (e) => {
      // Fires on refused/failed connections — expected when the API is
      // unreachable. onclose follows; reconnect is scheduled there.
      console.log("[career-agent] WS error (will retry):", wsUrl);
    };
    ws.onopen = () => {
      console.log("[career-agent] connected to", wsUrl);
      setBadge(true);
      startPing();
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      handleMessage(msg).catch((e) =>
        reply(msg.id, false, null, String(e && e.message ? e.message : e))
      );
    };
    ws.onclose = (ev) => {
      stopPing();
      setBadge(false);
      ws = null;
      // Code 4000 = the server replaced us with a newer connection (e.g. the
      // service worker woke up again). Do NOT reconnect in that case — the
      // newer connection is alive; reconnecting would ping-pong forever.
      if (ev && ev.code === 4000) return;
      scheduleReconnect();
    };
  });
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 5000);
}

function setBadge(on) {
  chrome.action.setBadgeText({ text: on ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: on ? "#2e9e5b" : "#999" });
}

function reply(id, ok, data, error) {
  if (!id || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ id, ok, data, error }));
}

// --- command handling ----------------------------------------------------

async function handleMessage(msg) {
  const { id, action, params = {} } = msg;
  try {
    let data;
    switch (action) {
      case "navigate":
        data = await cmdNavigate(params.url);
        break;
      case "fill":
        data = await cmdFill(params.selector, params.text);
        break;
      case "click":
        data = await cmdClick(params.selector);
        break;
      case "press":
        data = await cmdPress(params.key || "Enter");
        break;
      case "extract":
        data = await cmdExtract(params.card, params.fields, params.maxItems);
        break;
      case "run_flow":
        data = await cmdRunFlow(params.baseUrl, params.query, params.steps);
        break;
      case "discover_flow":
        data = await cmdDiscoverFlow(params.baseUrl, params.query, params.flowType);
        break;
      case "get_cookies":
        data = await cmdGetCookies(params.url);
        break;
      default:
        return reply(id, false, null, `Unknown action: ${action}`);
    }
    reply(id, true, data);
  } catch (e) {
    reply(id, false, null, String((e && e.message) || e));
  }
}

// --- low-level commands (content-script based) ----------------------------

async function ensureTab(url) {
  let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    tab = await chrome.tabs.create({ active: false });
  }
  if (url) {
    await chrome.tabs.update(tab.id, { url });
    await waitForComplete(tab.id);
  }
  return tab.id;
}

function waitForComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(
      () => reject(new Error("Page load timed out")),
      timeoutMs
    );
    chrome.tabs.onUpdated.addListener(function listener(id, info) {
      if (id === tabId && info.status === "complete") {
        clearTimeout(t);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    });
  });
}

async function withTab(fn) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab");
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: false },
    func: fn,
    args: [],
    world: "MAIN",
  });
  return results && results[0] && results[0].result;
}

async function cmdNavigate(url) {
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  await ensureTab(url);
  // Give SPA content a beat to render.
  await new Promise((r) => setTimeout(r, 2500));
  return { url };
}

async function cmdFill(selector, text) {
  if (!selector) throw new Error("fill: selector required");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (sel, txt) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, error: "Element not found: " + sel };
      el.focus();
      // Native setter so React/Angular see the change.
      const proto =
        el instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, txt);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true };
    },
    args: [selector, String(text ?? "")],
  });
  const r = res && res[0] && res[0].result;
  if (!r || !r.ok) throw new Error((r && r.error) || "fill failed");
  return r;
}

async function cmdClick(selector) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (sel) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, error: "Element not found: " + sel };
      el.scrollIntoView({ block: "center" });
      el.click();
      return { ok: true };
    },
    args: [selector],
  });
  const r = res && res[0] && res[0].result;
  if (!r || !r.ok) throw new Error((r && r.error) || "click failed");
  await new Promise((resolve) => setTimeout(resolve, 1500));
  return r;
}

async function cmdPress(key) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (k) => {
      const el = document.activeElement || document.body;
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: k, bubbles: true })
      );
      if (k === "Enter" && el.form) {
        el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
      }
      el.dispatchEvent(new KeyboardEvent("keyup", { key: k, bubbles: true }));
      return { ok: true };
    },
    args: [key],
  });
  await new Promise((resolve) => setTimeout(resolve, 2000));
  return res && res[0] && res[0].result;
}

async function cmdExtract(cardSelector, fields, maxItems) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (card, fieldMap, max) => {
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
    },
    args: [cardSelector, fields || {}, maxItems || 30],
  });
  return (res && res[0] && res[0].result) || [];
}

async function cmdRunFlow(baseUrl, query, steps) {
  const results = [];
  for (const step of steps || []) {
    const action = step.action;
    if (action === "navigate") {
      await cmdNavigate(step.url || baseUrl);
    } else if (action === "fill") {
      const text = step.param === "query" ? query : step.value || "";
      await cmdFill(step.selector, text);
    } else if (action === "click") {
      await cmdClick(step.selector);
    } else if (action === "press") {
      await cmdPress(step.key || "Enter");
    } else if (action === "wait") {
      await new Promise((r) => setTimeout(r, (step.seconds || 2) * 1000));
    } else if (action === "card" || step.card) {
      // Terminal step: extract using recorded card selectors.
      const data = await cmdExtract(
        step.card,
        step.fields || {},
        30
      );
      results.push(...data);
    }
  }
  return { results };
}

// --- keepalive: survive MV3 service-worker suspension ---------------------
// Brave/Chrome suspend idle service workers after ~30s, killing the WS.
// Two mechanisms combat this:
//  1. WS activity ping every 20s (resets the idle timer while connected)
//  2. chrome.alarms every 1 minute (wakes the worker even after suspension,
//     restarting connect() if the socket dropped)

const PING_INTERVAL_MS = 20000;
let pingTimer = null;

function startPing() {
  stopPing();
  pingTimer = setInterval(() => {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ note: "ping" }));
    }
  }, PING_INTERVAL_MS);
}

function stopPing() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

// Alarms fire even when the worker was suspended — this is the recovery path.
chrome.alarms.create("keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "keepalive" && (!ws || ws.readyState !== 1)) {
    console.log("[career-agent] alarm: reconnecting");
    connect();
  }
});

// --- boot -----------------------------------------------------------------

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

// --- agent-mode: discovery + cookie capture -------------------------------

/** Capture cookies for a domain as a Playwright storage_state "cookies" array. */
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

/**
 * Discover a search flow in the user's browser:
 * 1. Find the main search box on the site's homepage
 * 2. Fill the query + press Enter
 * 3. Wait for results, detect repeated card containers
 * Returns the same step JSON the server-side recorder produces.
 */
async function cmdDiscoverFlow(baseUrl, query, flowType) {
  const u = new URL(baseUrl);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab — open a tab and retry");

  // Ensure we're on the site (don't navigate if already deep on the site).
  if (!tab.url || !tab.url.includes(u.hostname)) {
    await cmdNavigate(baseUrl);
  }
  await new Promise((r) => setTimeout(r, 1500));

  // Step 1: find the most search-like visible input.
  const findRes = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => {
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
    },
    args: [],
  });
  const searchSel = findRes && findRes[0] && findRes[0].result;
  if (!searchSel) throw new Error("No search box found on the page");

  const steps = [
    { action: "navigate", url: baseUrl },
    { action: "fill", selector: searchSel, param: "query" },
    { action: "press", key: "Enter" },
    { action: "wait", seconds: 3 },
  ];

  await cmdFill(searchSel, query);
  await cmdPress("Enter");
  await new Promise((r) => setTimeout(r, 3500));

  // Step 3: detect repeated card containers.
  const cardRes = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: () => {
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
    },
    args: [],
  });
  const cardCandidates = (cardRes && cardRes[0] && cardRes[0].result) || [];
  if (cardCandidates.length === 0) {
    throw new Error("Could not find repeated result cards — search for something first, then retry");
  }

  // Field mapping: probe the first card for common sub-selectors.
  const card = cardCandidates[0];
  const fieldRes = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: (cardSel) => {
      const c = document.querySelector(cardSel);
      if (!c) return {};
      const link = c.querySelector("a h1, a h2, a h3, a [class*='title'], a");
      const fields = {};
      if (link) {
        const tag = link.tagName.toLowerCase();
        fields.title = tag + (link.className && typeof link.className === "string" && link.className.trim() ? "." + link.className.trim().split(/\s+/)[0] : "");
      }
      return fields;
    },
    args: [card],
  });
  const fields = (fieldRes && fieldRes[0] && fieldRes[0].result) || {};

  steps.push({ card, fields });
  return { steps, card, fields, raw: [] };
}
