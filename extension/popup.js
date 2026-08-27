const apiInput = document.getElementById("api");
const statusEl = document.getElementById("status");

chrome.storage.local.get(["apiBase"]).then(({ apiBase }) => {
  apiInput.value =
    apiBase ||
    (chrome.runtime.getURL("/").includes("chrome-extension://")
      ? "http://localhost:8000"
      : "");
});

async function checkStatus() {
  // The extension's own WS badge (set by the service worker) is the source of
  // truth — the API requires an X-API-Key for REST, which the popup doesn't
  // have, so a 401 on /status doesn't mean the agent link is down.
  chrome.action.getBadgeText({}, (text) => {
    if (text === "ON") {
      statusEl.textContent = "● Connected to Career Agent";
      statusEl.className = "ok";
    } else {
      statusEl.textContent = "○ Not connected — retrying every 5s…";
      statusEl.className = "down";
    }
  });
}

document.getElementById("save").addEventListener("click", () => {
  chrome.storage.local
    .set({ apiBase: apiInput.value.trim().replace(/\/+$/, "") })
    .then(() => {
      statusEl.textContent = "Saved — reconnecting…";
      // Service worker picks up the new config on its 5s reconnect cycle.
      setTimeout(checkStatus, 6000);
    });
});

checkStatus();
setInterval(checkStatus, 5000);
