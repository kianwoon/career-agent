# Wizard runs fully server-side (no local tunnel)

- **Date**: 2026-08-28T01:57:30+0800
- **Type**: decision

## Context

Original wizard design drove the user's local browser via CDP tunnel (Brave+cdp-proxy+ngrok) — worked but required per-machine setup, was macOS-only, and extensions deadlocked Playwright. Final architecture: everything runs in the Koyeb container's headless Chromium; user watches a live screenshot in the web UI and types credentials/MFA into the app, which are forwarded to the page (never stored). Flow recording is LLM-driven (discover_flow): LLM picks the search box from DOM inputs, does a test search, then picks the result-card selector. Cross-platform by construction.

## Decision / rationale

Never require local software for the product to work — 'client may be on Windows or Mac'. Server-side interactive browser pattern: screenshot polling (<img> + api_key query param since img can't set headers), credential/MFA forwarding endpoints, coordinate click-through. LLM discovery needs LLM_ENABLED/LLM_API_KEY set on the API service.
