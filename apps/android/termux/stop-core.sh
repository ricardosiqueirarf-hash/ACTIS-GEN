#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

STATE="${ACTIS_STATE_DIR:-$HOME/.actis-gen-termux}"
PID_DIR="$STATE/pids"

stop_pidfile() {
  local file="$1"
  local label="$2"
  if [ ! -f "$file" ]; then
    return 0
  fi
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    echo "[ACTIS] Parando $label ($pid)..."
    kill "$pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 0.2
    done
  fi
  rm -f "$file"
}

stop_pidfile "$PID_DIR/core.pid" "Core"
stop_pidfile "$PID_DIR/9router.pid" "9Router"
echo "[ACTIS] Serviços locais parados."
