#!/bin/bash
# Career Agent — container entrypoint
# Launches a persistent headless Chromium (with remote debugging), then
# starts the FastAPI server. The API connects to the browser via CDP.
#
# The Chromium profile lives in /data/browser-profile (persistent volume).
# On first boot a fresh profile is created; the user logs in once via human
# takeover, after which the same browser is reused for all sessions.

set -e

PROFILE_DIR="${BROWSER_PROFILE_DIR:-/data/browser-profile}"
CDP_PORT="${CDP_PORT:-9222}"
API_PORT="${API_PORT:-8000}"
# Residential proxy via Cloudflare: the Mac exposes an HTTP CONNECT proxy on
# cdp-proxy.draftproof.app (through the Cloudflare tunnel). cloudflared access
# tcp creates a LOCAL TCP pipe on this port that forwards to that hostname.
PROXY_LOCAL_PORT="${PROXY_LOCAL_PORT:-1080}"
CDP_PROXY_HOSTNAME="${CDP_PROXY_HOSTNAME:-cdp-proxy.draftproof.app}"

mkdir -p "$PROFILE_DIR"

# ---------------------------------------------------------------------------
# 1. Residential proxy: connect to the Mac's HTTP CONNECT proxy via Cloudflare.
#    cloudflared access tcp creates a local TCP pipe (localhost:$PROXY_LOCAL_PORT)
#    that tunnels through Cloudflare to http-proxy.py on the Mac (home IP).
#    Chromium uses it as an HTTP proxy with PROXY_USERNAME/PROXY_PASSWORD.
# ---------------------------------------------------------------------------
PROXY_SETUP=0
if command -v cloudflared >/dev/null 2>&1 && [ -n "${CDP_PROXY_HOSTNAME:-}" ]; then
  echo "=== Starting cloudflared access tcp -> $CDP_PROXY_HOSTNAME (localhost:$PROXY_LOCAL_PORT) ==="
  cloudflared access tcp \
    --hostname "$CDP_PROXY_HOSTNAME" \
    --url "localhost:$PROXY_LOCAL_PORT" \
    > /tmp/cloudflared-access.log 2>&1 &
  CLOUDFLARED_PID=$!
  echo "cloudflared access PID $CLOUDFLARED_PID"
  # Wait for the local TCP pipe to accept connections.
  for i in $(seq 1 20); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PROXY_LOCAL_PORT") 2>/dev/null; then
      exec 3>&- 3<&-
      echo "Proxy pipe ready on localhost:$PROXY_LOCAL_PORT after ${i}s"
      PROXY_SETUP=1
      break
    fi
    sleep 1
  done
fi

# The proxy URL Chromium will use. If we set up the access pipe, use it;
# otherwise fall back to PROXY_URL env (e.g. a real residential proxy service).
PROXY_URL_FINAL=""
if [ "$PROXY_SETUP" = "1" ]; then
  PROXY_URL_FINAL="http://127.0.0.1:$PROXY_LOCAL_PORT"
elif [ -n "${PROXY_URL:-}" ]; then
  PROXY_URL_FINAL="$PROXY_URL"
fi
if [ -n "$PROXY_URL_FINAL" ]; then
  echo "PROXY_URL=$PROXY_URL_FINAL"
  echo "PROXY_USERNAME=${PROXY_USERNAME:-}"
  echo "PROXY_PASSWORD=${PROXY_PASSWORD:+<set>}"
fi

echo "=== Starting persistent headless Chromium (CDP :$CDP_PORT) ==="
# Resolve the chromium binary installed by playwright.
CHROME_BIN="$(find /ms-playwright -name chrome -o -name chromium 2>/dev/null | head -1)"
if [ -z "$CHROME_BIN" ]; then
  echo "ERROR: chromium binary not found under /ms-playwright"
  exit 1
fi
echo "Using chromium: $CHROME_BIN"

# Chromium --proxy-server does NOT accept inline credentials. But when the
# API connects via CDP it drives Chromium; the proxy auth is applied by
# Playwright's launch(proxy={...}) in session.py/browser.py. For the
# persistent CDP Chromium, we pass --proxy-server without creds — the auth is
# handled by Playwright when it launches its OWN browser for replay/capture.
# The persistent CDP browser is used for human-takeover/capture; searches use
# Playwright-launched browsers that DO get the proxy creds.
PROXY_ARGS=()
if [ -n "$PROXY_URL_FINAL" ]; then
  echo "Chromium using proxy: $PROXY_URL_FINAL"
  PROXY_ARGS+=(--proxy-server="$PROXY_URL_FINAL")
fi

"$CHROME_BIN" \
  --headless=new \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-gpu \
  --disable-software-rasterizer \
  --no-sandbox \
  --disable-features=TranslateUI \
  "${PROXY_ARGS[@]}" \
  > /tmp/chrome.log 2>&1 &

CHROME_PID=$!
echo "Chrome PID $CHROME_PID"

# Wait for CDP to be ready.
for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
    echo "CDP ready after ${i}s"
    break
  fi
  sleep 1
done

echo "=== Starting uvicorn (API :$API_PORT) ==="
# The API connects to the IN-CONTAINER Chromium (never an external browser).
export BRAVE_CDP_URL="http://127.0.0.1:$CDP_PORT"
echo "BRAVE_CDP_URL=$BRAVE_CDP_URL"

exec uv run uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT"
