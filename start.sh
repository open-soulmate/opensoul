#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Open-Soulmate 一键启动脚本
# Usage: ./start.sh [all|soul|mate|soma] [--port PORT]
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SOUL_PORT="${SOUL_PORT:-8090}"
MATE_PORT="${MATE_PORT:-3002}"
COMPONENT="${1:-all}"
shift 2>/dev/null || true

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) SOUL_PORT="$2"; shift 2 ;;
    --soul-port) SOUL_PORT="$2"; shift 2 ;;
    --mate-port) MATE_PORT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[OpenSoul]${NC} $*"; }
logm() { echo -e "${GREEN}[OpenMate]${NC} $*"; }
logs() { echo -e "${YELLOW}[OpenSoma]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

SOUL_DIR="$HOME/opensoul"
MATE_DIR="$HOME/openmate"
SOMA_DIR="$HOME/opensoma"

# ── Helpers ──────────────────────────────────────────────────────

kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    log "Killing processes on port $port: $pids"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
}

wait_for_port() {
  local port=$1
  local name=$2
  local max_wait=${3:-30}
  local i=0
  while ! curl -sf "http://127.0.0.1:$port" >/dev/null 2>&1 && \
        ! curl -sf "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; do
    sleep 1
    i=$((i + 1))
    if [[ $i -ge $max_wait ]]; then
      err "$name failed to start on port $port after ${max_wait}s"
      return 1
    fi
  done
  log "$name is ready on port $port ✓"
}

# ── Start OpenSoul ───────────────────────────────────────────────

start_soul() {
  log "Starting OpenSoul on port $SOUL_PORT..."
  kill_port "$SOUL_PORT"

  cd "$SOUL_DIR"
  if [[ ! -d .venv ]]; then
    log "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt 2>/dev/null || true
  fi

  .venv/bin/python -m uvicorn src.main:app \
    --host 0.0.0.0 --port "$SOUL_PORT" \
    > /tmp/opensoul.log 2>&1 &
  echo $! > /tmp/opensoul.pid
  log "OpenSoul PID: $(cat /tmp/opensoul.pid)"
  wait_for_port "$SOUL_PORT" "OpenSoul"
}

# ── Start OpenMate ───────────────────────────────────────────────

start_mate() {
  logm "Starting OpenMate on port $MATE_PORT..."
  kill_port "$MATE_PORT"

  cd "$MATE_DIR"
  if [[ ! -d node_modules ]]; then
    logm "Installing dependencies..."
    npm install --silent
  fi

  NEXT_PUBLIC_API_URL="http://127.0.0.1:$SOUL_PORT" \
    npx next dev --port "$MATE_PORT" --hostname 0.0.0.0 \
    > /tmp/openmate.log 2>&1 &
  echo $! > /tmp/openmate.pid
  logm "OpenMate PID: $(cat /tmp/openmate.pid)"
  wait_for_port "$MATE_PORT" "OpenMate"
}

# ── Status ───────────────────────────────────────────────────────

show_status() {
  echo ""
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${BLUE}  Open-Soulmate Ecosystem Status${NC}"
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

  # OpenSoul
  if curl -sf "http://127.0.0.1:$SOUL_PORT/api/health" >/dev/null 2>&1; then
    local organs
    organs=$(curl -sf "http://127.0.0.1:$SOUL_PORT/api/diagnostics/check-all" 2>/dev/null | \
             python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"summary\"][\"healthy\"]}/{d[\"summary\"][\"total\"]}')" 2>/dev/null || echo "?/?")
    echo -e "  ${GREEN}●${NC} OpenSoul   http://0.0.0.0:$SOUL_PORT  (organs: $organs)"
  else
    echo -e "  ${RED}●${NC} OpenSoul   port $SOUL_PORT  (not running)"
  fi

  # OpenMate
  if curl -sf "http://127.0.0.1:$MATE_PORT" >/dev/null 2>&1; then
    echo -e "  ${GREEN}●${NC} OpenMate   http://0.0.0.0:$MATE_PORT"
  else
    echo -e "  ${RED}●${NC} OpenMate   port $MATE_PORT  (not running)"
  fi

  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────

case "$COMPONENT" in
  all)
    start_soul
    start_mate
    show_status
    ;;
  soul)
    start_soul
    show_status
    ;;
  mate)
    start_mate
    show_status
    ;;
  soma)
    logs "Starting OpenSoma..."
    cd "$SOMA_DIR"
    if [[ -f Cargo.toml ]]; then
      cargo run --release 2>&1 | tee /tmp/opensoma.log &
      echo $! > /tmp/opensoma.pid
      logs "OpenSoma PID: $(cat /tmp/opensoma.pid)"
    else
      err "OpenSoma not found at $SOMA_DIR"
      exit 1
    fi
    ;;
  status)
    show_status
    ;;
  stop)
    log "Stopping all services..."
    for pidfile in /tmp/opensoul.pid /tmp/openmate.pid /tmp/opensoma.pid; do
      if [[ -f "$pidfile" ]]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null || true
          log "Stopped PID $pid"
        fi
        rm -f "$pidfile"
      fi
    done
    # Also kill by port
    kill_port "$SOUL_PORT"
    kill_port "$MATE_PORT"
    log "All services stopped."
    ;;
  *)
    echo "Usage: $0 [all|soul|mate|soma|status|stop] [--soul-port PORT] [--mate-port PORT]"
    exit 1
    ;;
esac
