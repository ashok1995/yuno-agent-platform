#!/usr/bin/env bash
# Single-command local setup for Yuno Agent Platform
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
mkdir -p "$ROOT/logs"

log() { echo "[yuno] $*"; }

# ── Ollama check ───────────────────────────────────────────────────────────────
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  log "⚠️  Ollama not reachable at :11434 — start with: ollama serve"
else
  log "✅ Ollama is running"
fi

# ── Backend venv + deps ──────────────────────────────────────────────────────
if [ ! -d "$BACKEND/venv" ]; then
  log "Creating backend virtualenv..."
  python3 -m venv "$BACKEND/venv"
fi
# shellcheck disable=SC1091
source "$BACKEND/venv/bin/activate"
pip install -q -r "$BACKEND/requirements.txt"

if [ ! -f "$BACKEND/.env" ]; then
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  log "Created backend/.env — set TELEGRAM_BOT_TOKEN for Telegram channel"
fi

# ── Frontend venv + deps ─────────────────────────────────────────────────────
if [ ! -d "$FRONTEND/venv" ]; then
  log "Creating frontend virtualenv..."
  python3 -m venv "$FRONTEND/venv"
fi
# shellcheck disable=SC1091
source "$FRONTEND/venv/bin/activate"
pip install -q -r "$FRONTEND/requirements.txt"

# ── Kill existing processes on ports (target by port) ────────────────────────
kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    log "Stopping process on port $port (pid: $pids)"
    kill $pids 2>/dev/null || true
    sleep 1
  fi
}

kill_port 8000
kill_port 8501

# ── Start services ───────────────────────────────────────────────────────────
log "Starting backend on http://127.0.0.1:8000"
cd "$BACKEND"
# shellcheck disable=SC1091
source "$BACKEND/venv/bin/activate"
nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload \
  > "$ROOT/logs/backend.log" 2>&1 &
echo $! > "$ROOT/logs/backend.pid"

sleep 2

log "Starting frontend on http://127.0.0.1:8501"
cd "$FRONTEND"
# shellcheck disable=SC1091
source "$FRONTEND/venv/bin/activate"
nohup streamlit run app.py --server.port 8501 --server.headless true \
  > "$ROOT/logs/frontend.log" 2>&1 &
echo $! > "$ROOT/logs/frontend.pid"

log ""
log "🚀 Platform ready"
log "   Dashboard:  http://localhost:8501"
log "   API:        http://localhost:8000/docs"
log "   Logs:       $ROOT/logs/"
log ""
log "Stop with: ./stop.sh (or kill \$(cat logs/*.pid))"
