#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Open-Soulmate 一键启动脚本
# Usage: ./start.sh [all|soul|mate|stop|status|restart]
# ─────────────────────────────────────────────────────────────
set -euo pipefail

OPENSOUL_DIR="$HOME/opensoul"
OPENMATE_DIR="$HOME/openmate"
SOUL_PORT=8090
MATE_PORT=3002
PID_DIR="$HOME/.openmate/run"

mkdir -p "$PID_DIR"

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[OpenSoulmate]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }

# ── Helpers ─────────────────────────────────────────────────
is_port_in_use() {
    ss -tlnp 2>/dev/null | grep -q ":$1 " || lsof -i :"$1" &>/dev/null
}

wait_for_port() {
    local port=$1 name=$2 timeout=${3:-30}
    local i=0
    while ! is_port_in_use "$port"; do
        sleep 1
        i=$((i + 1))
        if [ $i -ge $timeout ]; then
            err "$name failed to start on port $port (${timeout}s timeout)"
            return 1
        fi
    done
    ok "$name is ready on port $port"
}

# ── Start OpenSoul ──────────────────────────────────────────
start_soul() {
    log "🧠 Starting OpenSoul (backend)..."
    if is_port_in_use $SOUL_PORT; then
        warn "Port $SOUL_PORT already in use — OpenSoul may already be running"
        return 0
    fi
    cd "$OPENSOUL_DIR"
    nohup .venv/bin/python -m uvicorn src.main:app \
        --host 0.0.0.0 --port $SOUL_PORT \
        > "$PID_DIR/opensoul.log" 2>&1 &
    echo $! > "$PID_DIR/opensoul.pid"
    wait_for_port $SOUL_PORT "OpenSoul"
}

# ── Start OpenMate ──────────────────────────────────────────
start_mate() {
    log "👤 Starting OpenMate (frontend)..."
    if is_port_in_use $MATE_PORT; then
        warn "Port $MATE_PORT already in use — OpenMate may already be running"
        return 0
    fi
    cd "$OPENMATE_DIR"
    nohup npm run dev -- --port $MATE_PORT \
        > "$PID_DIR/openmate.log" 2>&1 &
    echo $! > "$PID_DIR/openmate.pid"
    wait_for_port $MATE_PORT "OpenMate"
}

# ── Stop ────────────────────────────────────────────────────
stop_all() {
    log "Stopping all services..."
    for svc in opensoul openmate; do
        local pidfile="$PID_DIR/$svc.pid"
        if [ -f "$pidfile" ]; then
            local pid
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null && ok "Stopped $svc (PID $pid)" || warn "Failed to kill $svc"
            else
                warn "$svc (PID $pid) already stopped"
            fi
            rm -f "$pidfile"
        fi
    done
    # Also kill by port as fallback
    fuser -k $SOUL_PORT/tcp 2>/dev/null || true
    fuser -k $MATE_PORT/tcp 2>/dev/null || true
    ok "All services stopped"
}

# ── Status ──────────────────────────────────────────────────
show_status() {
    echo ""
    echo -e "${BLUE}━━━ Open-Soulmate System Status ━━━${NC}"
    echo ""
    for svc_port in "OpenSoul:$SOUL_PORT" "OpenMate:$MATE_PORT"; do
        local name="${svc_port%%:*}"
        local port="${svc_port##*:}"
        if is_port_in_use "$port"; then
            ok "$name — ${GREEN}running${NC} (port $port)"
        else
            err "$name — ${RED}stopped${NC} (port $port)"
        fi
    done

    # Check organ health
    echo ""
    log "Organ health check:"
    for organ in cortex nerve will gland vital vein sense immune marrow gene echo mirror link hippo reflex heredity pulse nest limb voice vision mind; do
        local status
        status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$SOUL_PORT/api/$organ/health" 2>/dev/null || echo "000")
        if [ "$status" = "200" ]; then
            ok "$organ"
        else
            err "$organ (HTTP $status)"
        fi
    done
    echo ""
}

# ── Restart ─────────────────────────────────────────────────
restart_all() {
    stop_all
    sleep 2
    start_soul
    start_mate
    show_status
}

# ── Main ────────────────────────────────────────────────────
case "${1:-all}" in
    all)
        start_soul
        start_mate
        show_status
        ;;
    soul)
        start_soul
        ;;
    mate)
        start_mate
        ;;
    stop)
        stop_all
        ;;
    status)
        show_status
        ;;
    restart)
        restart_all
        ;;
    *)
        echo "Usage: $0 [all|soul|mate|stop|status|restart]"
        echo ""
        echo "  all      Start all services (default)"
        echo "  soul     Start OpenSoul backend only"
        echo "  mate     Start OpenMate frontend only"
        echo "  stop     Stop all services"
        echo "  status   Show system status"
        echo "  restart  Restart all services"
        exit 1
        ;;
esac
