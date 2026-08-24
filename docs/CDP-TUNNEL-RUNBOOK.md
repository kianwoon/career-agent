# CDP Tunnel Runbook

The Career Agent API runs on Koyeb but drives a **local authenticated browser**
(which holds the signed-in LinkedIn session). The two are connected by a CDP
tunnel:

```
Brave (--remote-debugging-port=9222)
  -> cdp-proxy.py (rewrites ws:// -> wss:// public host, adds auth)
  -> ngrok (public https URL that Koyeb reaches)
```

The Koyeb deployment reads `BRAVE_CDP_URL` (set to the ngrok URL) and
`CDP_AUTH_HEADER` (e.g. `X-Auth-Token: ...`) from its environment.

---

## Quick start (any machine)

### 1. Prerequisites (install once)

```bash
# ngrok — the tunnel to the internet
brew install ngrok
ngrok config add-authtoken <your-token>     # one time

# cdp-proxy dependencies (fastapi, uvicorn, httpx, websockets)
cd backend && uv sync
```

### 2. Launch Brave with CDP enabled

Brave must be **signed in to LinkedIn** and started with remote debugging:

```bash
open -a "Brave Browser" --args \
  --remote-debugging-port=9222 \
  --remote-allow-origins='*'
```

> If Brave is already running without CDP, quit it first
> (`osascript -e 'quit app "Brave Browser"'`), then relaunch with the flags.

### 3. Bring the tunnel up

```bash
./tunnel.sh start
```

This:
1. Verifies Brave CDP on `:9222`
2. Starts ngrok (forwards to `:9999`)
3. Starts `cdp-proxy.py` on `:9999` with the correct public host
4. **Auto-updates the Koyeb deployment env** `BRAVE_CDP_URL` to the new ngrok URL

### 4. Verify

```bash
./tunnel.sh verify
```

Expected output — all 4 checks green, ending with:
```
websocket rewrite OK: wss://<host>.ngrok-free.app/devtools/browser/...
```

---

## Usage

| Command | What it does |
|---------|--------------|
| `./tunnel.sh start` | Bring the whole chain up + update Koyeb env |
| `./tunnel.sh stop` | Tear down proxy + ngrok (Brave stays running) |
| `./tunnel.sh restart` | Stop then start (use after Mac reboot) |
| `./tunnel.sh status` | Show each link's state |
| `./tunnel.sh verify` | End-to-end curl through the tunnel |
| `./tunnel.sh url` | Print the current public ngrok URL |
| `./tunnel.sh update-koyeb` | Push current URL to Koyeb env (if URL changed) |

---

## Why this exists

The API needs an authenticated, LinkedIn-trusted browser. Options:

| Path | Needs | Used when |
|------|-------|-----------|
| **CDP tunnel → local Brave** (this) | Local Mac on, Brave signed in | Primary; the browser that holds the login |
| **Stored session replay** | A captured cookie blob in Postgres | Fallback if the tunnel is down |
| **Residential proxy** (future) | Brightdata / proxy service | Planned; would remove the local-browser dependency |

---

## Troubleshooting

### "Brave CDP : DOWN"
Brave isn't exposing CDP. Quit Brave and relaunch with the debug flags
(section 2). **Important:** the remote-debugging flag must be on the FIRST
launch — if Brave was already running, relaunch it.

### "cdp-proxy : DOWN"
Check `/tmp/career-agent-tunnel/cdp-proxy.log`. Usually the venv is missing
deps → `cd backend && uv sync`.

### "ngrok : DOWN"
Check `/tmp/career-agent-tunnel/ngrok.log`. Verify `ngrok config check`
passes and you have a free/basic plan (free ngrok URLs work fine).

### Search still failing after tunnel is up
- Wait for the Koyeb redeploy to finish (`koyeb deployment list --app career-agent`).
- Re-run `./tunnel.sh update-koyeb` if the URL changed but didn't auto-update.
- Confirm the Koyeb env has both `BRAVE_CDP_URL` (the ngrok URL) and
  `CDP_AUTH_HEADER` (`X-Auth-Token: career-cdp-secret-2026`).
- Check the API instance logs:
  `koyeb instances logs <instance-id> --tail`

### ngrok URL keeps changing
ngrok free URLs are random per session. Every `start`/`restart` gets a new
URL, and `tunnel.sh` pushes it to Koyeb automatically. If you want a stable
URL, upgrade to a paid ngrok plan with a reserved domain, or switch to a
Cloudflare tunnel with a named tunnel (requires root on the host).

---

## Architecture note

The tunnel only exposes the **CDP control endpoint** — the API can navigate
pages and read content, but the actual browser runs on your Mac. When you
switch to a residential proxy, this whole file becomes obsolete: the API
would launch its own headless Chromium in the Koyeb container routed through
the proxy IP, with no local browser needed.
