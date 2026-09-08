# Koyeb prod career-agent API: Koyeb route rule strips '/api' prefix, AND the FastAPI router itself carries prefix '/api/v1' — so prod paths are DOUBLED: POST https://career-agent-kianwoon-88223cd5.koyeb.app/api/v1/api/v1/search/candidates (same for /tasks/{id}/results). Prod API key lives in Koyeb env API_KEYS (career_...:60), NOT dev-e2e-key. Tunnel chain (Brave:9222 -> cdp-proxy:9999 -> ngrok -> Koyeb) is restored via ./tunnel.sh start (auto-updates Koyeb BRAVE_CDP_URL env) + ./tunnel.sh verify; Brave must be launched with --remote-debugging-port=9222 --remote-allow-origins='*' --disable-extensions (extensions deadlock Playwright CDP attach per docs/CDP-TUNNEL-RUNBOOK.md). Verified: prod task 942db35d completed 165s, 9 candidates via tunnel.

- **Date**: 2026-09-01T16:24:04+0800
- **Type**: lesson

## What happened



## Root cause / fix


