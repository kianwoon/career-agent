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
    ws.onclose = () => {
      setBadge(false);
      ws = null;
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

// --- boot -----------------------------------------------------------------

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
