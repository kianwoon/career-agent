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
  try {
    const apiBase = apiInput.value.trim().replace(/\/+$/, "");
    const res = await fetch(`${apiBase}/api/v1/agent/status`);
    const data = await res.json();
    statusEl.textContent = data.connected
      ? "● Connected to Career Agent"
      : "○ API reachable, waiting for agent…";
    statusEl.className = data.connected ? "ok" : "down";
  } catch (e) {
    statusEl.textContent = "○ API unreachable";
    statusEl.className = "down";
  }
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
