#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Open-Soulmate — One-Line Startup Script
# Usage: ./start.sh [all|soul|mate|soma] [--bg]
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ── Config ───────────────────────────────────────────────────────────
SOUL_DIR="${OPENSOUL_DIR:-$HOME/opensoul}"
MATE_DIR="${OPENMATE_DIR:-$HOME/openmate}"
SOMA_DIR="${OPENSOMA_DIR:-$HOME/opensoma}"

SOUL_PORT="${SOUL_PORT:-8090}"
MATE_PORT="${MATE_PORT:-3002}"

# ── Helpers ──────────────────────────────────────────────────────────

log()  { echo -e "${CYAN}[open-soul]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }

check_port() {
    local port=$1
    if ss -tlnp 2>/dev/null | grep -q ":${port} " ; then
        return 0
    fi
    return 1
}

wait_for_health() {
    local url=$1
    local name=$2
    local max_wait=${3:-30}
    local waited=0
    while [ $waited -lt $max_wait ]; do
        if curl -sf "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    err "$name failed to start after ${max_wait}s"
    return 1
}

# ── Start Functions ──────────────────────────────────────────────────

start_soul() {
    log "🧠 Starting OpenSoul (port ${SOUL_PORT})..."
    
    if check_port "$SOUL_PORT"; then
        warn "OpenSoul already running on port ${SOUL_PORT}"
        return 0
    fi
    
    if [ ! -d "$SOUL_DIR" ]; then
        err "OpenSoul directory not found: $SOUL_DIR"
        return 1
    fi
    
    cd "$SOUL_DIR"
    
    # Use venv if available
    if [ -f ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python3"
    fi
    
    if [ "$BACKGROUND" = "1" ]; then
        nohup $PYTHON -m uvicorn src.main:app \
            --host 0.0.0.0 --port "$SOUL_PORT" \
            > "$LOG_DIR/opensoul.log" 2>&1 &
        echo $! > "$LOG_DIR/opensoul.pid"
        ok "OpenSoul started (PID: $(cat "$LOG_DIR/opensoul.pid"))"
    else
        ok "Starting OpenSoul in foreground..."
        $PYTHON -m uvicorn src.main:app \
            --host 0.0.0.0 --port "$SOUL_PORT"
    fi
}

start_mate() {
    log "👤 Starting OpenMate (port ${MATE_PORT})..."
    
    if check_port "$MATE_PORT"; then
        warn "OpenMate already running on port ${MATE_PORT}"
        return 0
    fi
    
    if [ ! -d "$MATE_DIR" ]; then
        err "OpenMate directory not found: $MATE_DIR"
        return 1
    fi
    
    cd "$MATE_DIR"
    
    if [ "$BACKGROUND" = "1" ]; then
        nohup npx next dev --port "$MATE_PORT" --hostname 0.0.0.0 \
            > "$LOG_DIR/openmate.log" 2>&1 &
        echo $! > "$LOG_DIR/openmate.pid"
        ok "OpenMate started (PID: $(cat "$LOG_DIR/openmate.pid"))"
    else
        ok "Starting OpenMate in foreground..."
        npx next dev --port "$MATE_PORT" --hostname 0.0.0.0
    fi
}

start_soma() {
    log "🤖 Starting OpenSoma..."
    
    if [ ! -d "$SOMA_DIR" ]; then
        err "OpenSoma directory not found: $SOMA_DIR"
        return 1
    fi
    
    cd "$SOMA_DIR"
    
    # Check if binary exists
    if [ -f "target/release/opensoma" ]; then
        SOMA_BIN="target/release/opensoma"
    elif [ -f "target/debug/opensoma" ]; then
        SOMA_BIN="target/debug/opensoma"
    else
        warn "OpenSoma binary not found. Building..."
        cargo build --release
        SOMA_BIN="target/release/opensoma"
    fi
    
    if [ "$BACKGROUND" = "1" ]; then
        nohup ./$SOMA_BIN --config config.toml \
            > "$LOG_DIR/opensoma.log" 2>&1 &
        echo $! > "$LOG_DIR/opensoma.pid"
        ok "OpenSoma started (PID: $(cat "$LOG_DIR/opensoma.pid"))"
    else
        ok "Starting OpenSoma in foreground..."
        ./$SOMA_BIN --config config.toml
    fi
}

stop_all() {
    log "Stopping all services..."
    for pidfile in "$LOG_DIR"/*.pid; do
        [ -f "$pidfile" ] || continue
        local pid
        pid=$(cat "$pidfile")
        local name
        name=$(basename "$pidfile" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            ok "Stopped $name (PID: $pid)"
        else
            warn "$name (PID: $pid) already stopped"
        fi
        rm -f "$pidfile"
    done
}

status() {
    echo -e "\n${BOLD}Open-Soulmate System Status${NC}"
    echo "────────────────────────────────────────"
    
    for svc in "soul:$SOUL_PORT" "mate:$MATE_PORT"; do
        local name="${svc%%:*}"
        local port="${svc##*:}"
        if check_port "$port"; then
            echo -e "  ${GREEN}●${NC} ${name} — port ${port} ${GREEN}(running)${NC}"
        else
            echo -e "  ${RED}●${NC} ${name} — port ${port} ${RED}(stopped)${NC}"
        fi
    done
    
    # Check OpenSoma
    if [ -f "$LOG_DIR/opensoma.pid" ] && kill -0 "$(cat "$LOG_DIR/opensoma.pid")" 2>/dev/null; then
        echo -e "  ${GREEN}●${NC} soma — PID $(cat "$LOG_DIR/opensoma.pid") ${GREEN}(running)${NC}"
    else
        echo -e "  ${RED}●${NC} soma ${RED}(stopped)${NC}"
    fi
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────

COMMAND="${1:-all}"
BACKGROUND=0

for arg in "$@"; do
    case "$arg" in
        --bg|--background) BACKGROUND=1 ;;
    esac
done

case "$COMMAND" in
    all)
        echo -e "\n${BOLD}${CYAN}╔════════════════════════════════════════╗${NC}"
        echo -e "${BOLD}${CYAN}║   Open-Soulmate · One Soul, Infinite  ║${NC}"
        echo -e "${BOLD}${CYAN}║   Soma.                                ║${NC}"
        echo -e "${BOLD}${CYAN}╚════════════════════════════════════════╝${NC}\n"
        BACKGROUND=1
        start_soul
        sleep 2
        start_mate
        sleep 2
        start_soma
        echo ""
        status
        ;;
    soul)
        start_soul
        ;;
    mate)
        start_mate
        ;;
    soma)
        start_soma
        ;;
    stop)
        stop_all
        ;;
    status)
        status
        ;;
    restart)
        stop_all
        sleep 2
        BACKGROUND=1
        start_soul
        sleep 2
        start_mate
        sleep 2
        start_soma
        echo ""
        status
        ;;
    *)
        echo -e "${BOLD}Usage:${NC} $0 [all|soul|mate|soma|stop|status|restart] [--bg]"
        echo ""
        echo "  all       Start all services (background mode)"
        echo "  soul      Start OpenSoul only"
        echo "  mate      Start OpenMate only"
        echo "  soma      Start OpenSoma only"
        echo "  stop      Stop all services"
        echo "  status    Show service status"
        echo "  restart   Restart all services"
        echo ""
        echo "  --bg      Run in background (default for 'all')"
        echo ""
        echo "Environment variables:"
        echo "  OPENSOUL_DIR   OpenSoul directory (default: ~/opensoul)"
        echo "  OPENMATE_DIR   OpenMate directory (default: ~/openmate)"
        echo "  OPENSOMA_DIR   OpenSoma directory (default: ~/opensoma)"
        echo "  SOUL_PORT      OpenSoul port (default: 8090)"
        echo "  MATE_PORT      OpenMate port (default: 3002)"
        exit 1
        ;;
esac
