# Brave extensions deadlock Playwright connectOverCDP

- **Date**: 2026-08-28T00:59:40+0800
- **Type**: lesson

## What happened

Wizard CDP connect timed out even though /json/version worked and raw websocket handshake succeeded. Playwright attached to 60+ targets (extensions' workers/service_workers) and its init never completed — hung even at 90s timeout. Relaunching Brave with --disable-extensions fixed it instantly.

## Root cause / fix

When Playwright connect_over_cdp times out against a real user browser: check target count via /json/list — if worker/service_worker targets from extensions dominate, relaunch the browser with --disable-extensions. Debug with DEBUG=pw:protocol and count Target.attachedToTarget events.
