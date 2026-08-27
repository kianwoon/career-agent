#!/bin/bash
# =============================================================================
# career-agent CDP tunnel manager
#
# Brings up / tears down / verifies the CDP tunnel chain that lets the Koyeb
# API drive your LOCAL Brave browser (which holds the signed-in LinkedIn
# session):
#
#     Brave (--remote-debugging-port=9222)
#       -> cdp-proxy.py (rewrites ws:// -> wss:// public host, adds auth)
#       -> ngrok (public https URL that Koyeb reaches)
#
# The Koyeb deployment reads BRAVE_CDP_URL (set to the ngrok URL) and
# CDP_AUTH_HEADER (X-Auth-Token) from its environment.
#
# Usage:
#   ./tunnel.sh start              # bring the whole chain up + update Koyeb env
#   ./tunnel.sh stop               # tear everything down (keeps Brave running)
#   ./tunnel.sh status             # check each link in the chain
#   ./tunnel.sh verify             # end-to-end: curl CDP through the tunnel
#   ./tunnel.sh url                # print the current public tunnel URL
#   ./tunnel.sh update-koyeb       # push current URL to Koyeb env (after URL change)
#   ./tunnel.sh restart            # stop then start (e.g. after Mac reboot)
#
# Requirements (install once):
#   - ngrok            (brew install ngrok)  — authenticated (ngrok config check)
#   - cdp-proxy deps   (backend/.venv)        — run `cd backend && uv sync`
#
# The cdp-proxy and ngrok run as background processes with PID files under
# $TUNNEL_RUN_DIR so we can manage them reliably.
# =============================================================================
set -euo pipefail

# --- config -----------------------------------------------------------------
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
PROXY_SCRIPT="$REPO_DIR/cdp-proxy.py"

TUNNEL_RUN_DIR="${TUNNEL_RUN_DIR:-/tmp/career-agent-tunnel}"
NROK_PID_FILE="$TUNNEL_RUN_DIR/ngrok.pid"
PROXY_PID_FILE="$TUNNEL_RUN_DIR/cdp-proxy.pid"
NROK_LOG="$TUNNEL_RUN_DIR/ngrok.log"
PROXY_LOG="$TUNNEL_RUN_DIR/cdp-proxy.log"

NROK_API="http://127.0.0.1:4040/api/tunnels"

CDP_PORT="${CDP_PORT:-9222}"
PROXY_PORT="${PROXY_PORT:-9999}"
AUTH_TOKEN="${CDP_AUTH_TOKEN:-career-cdp-secret-2026}"

KOYEB_APP="${KOYEB_APP:-career-agent}"
KOYEB_SERVICE="${KOYEB_SERVICE:-career-api}"

log()  { printf '\033[1;32m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '\033[1;31m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

ensure_run_dir() { mkdir -p "$TUNNEL_RUN_DIR"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
is_running() {
  local pid_file="$1" pattern="$2"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q "$pattern"
}

get_ngrok_url() {
  curl -s -m 3 "$NROK_API" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    for t in d.get("tunnels", []):
        if t.get("public_url", "").startswith("https://"):
            print(t["public_url"])
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
' 2>/dev/null
}

brave_cdp_ready() { curl -s -m 3 "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; }
proxy_ready()     { curl -s -m 3 -H "X-Auth-Token: $AUTH_TOKEN" "http://127.0.0.1:$PROXY_PORT/json/version" >/dev/null 2>&1; }

py_bin() {
  if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    echo "$BACKEND_DIR/.venv/bin/python"
  elif python3 -c "import fastapi" 2>/dev/null; then
    echo "python3"
  else
    die "No python with fastapi/uvicorn/httpx/websockets. Run: cd backend && uv sync"
  fi
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
cmd_brave() {
  if brave_cdp_ready; then
    log "Brave CDP is ready (:${CDP_PORT})"
    return 0
  fi
  warn "Brave is not exposing CDP on :$CDP_PORT."
  warn "Launch Brave with remote debugging (extensions MUST be disabled —"
  warn "they deadlock Playwright's CDP attach):"
  warn "  osascript -e 'quit app \"Brave Browser\"'"
  warn "  open -a 'Brave Browser' --args --remote-debugging-port=$CDP_PORT --remote-allow-origins='*' --disable-extensions"
  warn "Then re-run: $0 start"
  return 1
}

# Start ngrok first (forwards to :$PROXY_PORT even before the proxy listens).
cmd_start_ngrok() {
  ensure_run_dir
  if is_running "$NROK_PID_FILE" "ngrok http"; then
    log "ngrok already running (pid $(cat "$NROK_PID_FILE"))"
    return 0
  fi
  command -v ngrok >/dev/null 2>&1 || die "ngrok not found — brew install ngrok && ngrok config add-authtoken <token>"
  log "Starting ngrok -> http://127.0.0.1:$PROXY_PORT"
  nohup ngrok http "$PROXY_PORT" --log=stdout > "$NROK_LOG" 2>&1 &
  echo $! > "$NROK_PID_FILE"
  for _ in $(seq 1 20); do
    if get_ngrok_url >/dev/null 2>&1; then
      log "ngrok ready: $(get_ngrok_url)"
      return 0
    fi
    sleep 1
  done
  die "ngrok failed to start — see $NROK_LOG"
}

# Start cdp-proxy with a specific public host (defaults to localhost).
cmd_start_proxy() {
  ensure_run_dir
  local host="${1:-localhost:$PROXY_PORT}"
  if is_running "$PROXY_PID_FILE" "cdp-proxy.py"; then
    log "cdp-proxy already running (pid $(cat "$PROXY_PID_FILE"))"
    return 0
  fi
  brave_cdp_ready || die "Brave CDP not ready on :$CDP_PORT — start Brave first"
  local py
  py="$(py_bin)"
  log "Starting cdp-proxy on :$PROXY_PORT -> CDP :$CDP_PORT (public $host)"
  nohup "$py" "$PROXY_SCRIPT" \
      --listen-port "$PROXY_PORT" \
      --cdp-url "http://127.0.0.1:$CDP_PORT" \
      --public-host "$host" \
      --auth-token "$AUTH_TOKEN" \
      > "$PROXY_LOG" 2>&1 &
  echo $! > "$PROXY_PID_FILE"
  for _ in $(seq 1 15); do
    if proxy_ready; then
      log "cdp-proxy ready (pid $(cat "$PROXY_PID_FILE"))"
      return 0
    fi
    sleep 1
  done
  die "cdp-proxy failed to start — see $PROXY_LOG"
}

cmd_stop_one() {
  local pid_file="$1" name="$2"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      log "Stopping $name (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

cmd_update_koyeb() {
  local url
  url="$(get_ngrok_url)" || die "ngrok not running"
  log "Updating Koyeb $KOYEB_APP/$KOYEB_SERVICE env BRAVE_CDP_URL=$url"
  command -v koyeb >/dev/null 2>&1 || die "koyeb CLI not found"
  koyeb service update "$KOYEB_APP/$KOYEB_SERVICE" \
    --env "BRAVE_CDP_URL=$url" --wait >/dev/null 2>&1 \
    || die "koyeb service update failed (CLI logged in?)"
  log "Koyeb env updated"
}

# --- commands --------------------------------------------------------------
cmd_start() {
  cmd_brave
  # ngrok first (it doesn't need the proxy port to be live), then proxy with
  # the real ngrok host so websocket URLs are rewritten correctly.
  cmd_start_ngrok
  local url host
  url="$(get_ngrok_url)" || die "ngrok not running"
  host="${url#https://}"
  cmd_start_proxy "$host"
  log "Tunnel is UP: $url"
  cmd_update_koyeb
  log "Done. Verify: $0 verify"
}

cmd_stop() {
  cmd_stop_one "$PROXY_PID_FILE" "cdp-proxy"
  cmd_stop_one "$NROK_PID_FILE" "ngrok"
  log "Stopped. Brave stays running (it's your normal browser)."
}

cmd_status() {
  echo "==== CDP tunnel chain status ===="
  if brave_cdp_ready; then
    log "Brave CDP   : READY on :$CDP_PORT"
  else
    warn "Brave CDP   : DOWN on :$CDP_PORT (start Brave with --remote-debugging-port=$CDP_PORT)"
  fi
  if proxy_ready; then
    log "cdp-proxy   : READY on :$PROXY_PORT"
  else
    warn "cdp-proxy   : DOWN on :$PROXY_PORT"
  fi
  if url="$(get_ngrok_url)"; then
    log "ngrok       : READY -> $url"
  else
    warn "ngrok       : DOWN"
  fi
}

cmd_verify() {
  local url host
  url="$(get_ngrok_url)" || die "ngrok not running"
  host="${url#https://}"
  log "Verifying chain: Brave :$CDP_PORT -> proxy :$PROXY_PORT -> ngrok $host"
  echo "-- 1. Brave CDP directly"
  curl -s -m 5 "http://127.0.0.1:$CDP_PORT/json/version" | head -c 60 || die "Brave CDP unreachable"; echo
  echo "-- 2. cdp-proxy locally (auth header)"
  curl -s -m 5 -H "X-Auth-Token: $AUTH_TOKEN" "http://127.0.0.1:$PROXY_PORT/json/version" | head -c 60 || die "proxy unreachable"; echo
  echo "-- 3. Through ngrok (auth header)"
  curl -s -m 8 -H "X-Auth-Token: $AUTH_TOKEN" "$url/json/version" | head -c 60 || die "ngrok/endpoint unreachable"; echo
  echo "-- 4. websocket URL is rewritten to ngrok host"
  local ws
  ws="$(curl -s -m 8 -H "X-Auth-Token: $AUTH_TOKEN" "$url/json/version" | python3 -c "import json,sys; print(json.load(sys.stdin).get('webSocketDebuggerUrl',''))")"
  if [[ "$ws" == "wss://$host"* ]]; then
    log "websocket rewrite OK: $ws"
  else
    warn "websocket URL NOT rewritten: $ws (proxy public-host wrong?)"
  fi
}

cmd_url() {
  get_ngrok_url || die "ngrok not running"
}

# --- dispatch --------------------------------------------------------------
case "${1:-}" in
  start)          shift; cmd_start "$@" ;;
  stop)           shift; cmd_stop "$@" ;;
  status)         shift; cmd_status "$@" ;;
  verify)         shift; cmd_verify "$@" ;;
  url)            shift; cmd_url "$@" ;;
  update-koyeb)   shift; cmd_update_koyeb "$@" ;;
  restart)        shift; cmd_stop "$@"; cmd_start "$@" ;;
  *) sed -n '2,35p' "$0" | grep -E '^#' | sed 's/^# \{0,2\}//'; exit 0 ;;
esac
