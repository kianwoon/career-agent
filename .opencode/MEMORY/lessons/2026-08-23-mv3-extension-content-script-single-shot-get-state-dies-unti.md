# MV3 extension — content script single-shot GET_STATE dies until reload

- **Date**: 2026-08-23T19:23:36+0800
- **Type**: lesson

## What happened

Zoom Zoom extension: content.js sent GET_STATE once at init. If MV3 service worker was sleeping/restarting, chrome.runtime.lastError returned null state, handlers guarded with if (!state.settings) return; — all input (wheel, keyboard, dblclick) permanently dead until page reload.

## Root cause / fix

Fix: (1) Add retry logic to send() with configurable attempts + backoff. (2) Add ensureState() that coalesces concurrent callers and retries up to 5×200ms. (3) Re-ensure state on visibilitychange. (4) Listen for SETTINGS_UPDATED broadcast from background. (5) background.js: wrap chrome.tabs.getZoom/setZoom in safeGetZoom/safeSetZoom that catch rejects from discarded tabs. (6) broadcastSettings() sends new settings to all open tabs after UPDATE_SETTINGS. (7) popup.js send() also retries. (8) restoreZoomForTab only saves if it seeded a fresh entry.
