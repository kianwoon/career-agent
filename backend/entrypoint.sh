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

mkdir -p "$PROFILE_DIR"

echo "=== Starting persistent headless Chromium (CDP :$CDP_PORT) ==="
# Resolve the chromium binary installed by playwright.
CHROME_BIN="$(find /ms-playwright -name chrome -o -name chromium 2>/dev/null | head -1)"
if [ -z "$CHROME_BIN" ]; then
  echo "ERROR: chromium binary not found under /ms-playwright"
  exit 1
fi
echo "Using chromium: $CHROME_BIN"

# Optional proxy: route ALL browser traffic through a remote IP (e.g.
# residential proxy). Set PROXY_URL=socks5://host:port (Chromium's
# --proxy-server does NOT accept credentials; use an unauthenticated endpoint
# or whitelist by IP with microsocks -w).
PROXY_ARGS=()
if [ -n "${PROXY_URL:-}" ]; then
  echo "Using proxy: $PROXY_URL"
  PROXY_ARGS+=(--proxy-server="$PROXY_URL")
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
# Use the tunnel URL if provided (e.g. Koyeb env BRAVE_CDP_URL pointing to a
# local Brave via ngrok), otherwise fall back to the in-container Chromium.
export BRAVE_CDP_URL="${BRAVE_CDP_URL:-http://127.0.0.1:$CDP_PORT}"
echo "BRAVE_CDP_URL=$BRAVE_CDP_URL"

exec uv run uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT"
