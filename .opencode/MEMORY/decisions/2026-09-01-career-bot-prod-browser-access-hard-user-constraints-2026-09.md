# Career bot prod browser access — HARD USER CONSTRAINTS (2026-09-01): NEVER propose or implement a tunnel (ngrok/cdp-proxy/ngrok-style) again; user forbade it permanently and forcefully. Also rejected: background launchd daemons, dedicated automation browser/profile, headless-on-Koyeb pitches (unvalidated). Only acceptable direction: drive the user's own already-logged-in browser from INSIDE the browser (existing extension + /v1/agent/execute|poll|result protocol); no debug flags, no external reachability requirements. Extraction logic (boolean queries, NOT excludes, card parsing) is proven; only the transport/trigger needs porting to the extension.

- **Date**: 2026-09-01T16:44:23+0800
- **Type**: decision

## Context



## Decision / rationale


