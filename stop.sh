#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/logs"

for pidfile in "$ROOT/logs/backend.pid" "$ROOT/logs/frontend.pid"; do
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "[yuno] Stopped pid $pid"
    fi
    rm -f "$pidfile"
  fi
done

for port in 8000 8501; do
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    echo "[yuno] Freed port $port"
  fi
done
