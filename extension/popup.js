const apiInput = document.getElementById("api");
const statusEl = document.getElementById("status");
const DEFAULT_API = "https://career-agent-kianwoon-88223cd5.koyeb.app";

chrome.storage.local.get(["apiBase"]).then(({ apiBase }) => {
  apiInput.value = apiBase || DEFAULT_API;
});

async function checkStatus() {
  // Ask the BACKEND whether the agent has polled recently — the badge is
  // stale-prone (it persists after Chrome suspends the service worker), so
  // it caused "extension ON but page shows agent off" mismatches.
  // /agent/poll is public (no API key needed) and refreshes liveness, so it
  // doubles as both a reachability and a liveness probe.
  let apiBase = DEFAULT_API;
  try {
    const stored = await chrome.storage.local.get(["apiBase"]);
    apiBase = (stored.apiBase || DEFAULT_API).replace(/\/+$/, "");
  } catch {
    /* fall back to default */
  }
  try {
    const res = await fetch(`${apiBase}/api/v1/agent/poll`);
    if (res.ok) {
      statusEl.textContent = "● Connected to Career Agent";
      statusEl.className = "ok";
      return;
    }
  } catch {
    /* fall through to down state */
  }
  statusEl.textContent = "○ Not connected — retrying every 5s…";
  statusEl.className = "down";
}

document.getElementById("save").addEventListener("click", () => {
  chrome.storage.local
    .set({ apiBase: apiInput.value.trim().replace(/\/+$/, "") })
    .then(() => {
      statusEl.textContent = "Saved — reconnecting…";
      // Service worker picks up the new config on its next wake; the alarm
      // heartbeat fires within ~30s, or instantly if the worker is alive.
      chrome.runtime.sendMessage({ type: "restart-loop" }).catch(() => {});
      setTimeout(checkStatus, 6000);
    });
});

checkStatus();
setInterval(checkStatus, 5000);
