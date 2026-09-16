#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

ROOT="${ACTIS_ROOT:-$HOME/ACTIS-GEN}"
STATE="${ACTIS_STATE_DIR:-$HOME/.actis-gen-termux}"
VENV="$STATE/venv"
ENV_FILE="$STATE/actis.env"
LOG_DIR="$STATE/logs"
PID_DIR="$STATE/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [ ! -x "$VENV/bin/actis-gen-web" ]; then
  echo "[ACTIS] Core ainda não instalado. Execute install.sh primeiro." >&2
  exit 2
fi

http_alive() {
  curl -sS --max-time 1 -o /dev/null "$1" >/dev/null 2>&1
}

pid_alive() {
  [ -f "$1" ] || return 1
  local pid
  pid="$(cat "$1" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1
}

# 9Router é opcional para o servidor web subir, mas é necessário para execuções de modelo.
if command -v 9router >/dev/null 2>&1 && ! http_alive "http://127.0.0.1:20128"; then
  if pid_alive "$PID_DIR/9router.pid"; then
    echo "[ACTIS] 9Router iniciando..."
  else
    echo "[ACTIS] Iniciando 9Router..."
    nohup env \
      PORT=20128 \
      HOSTNAME=127.0.0.1 \
      NEXT_PUBLIC_BASE_URL=http://127.0.0.1:20128 \
      9router >"$LOG_DIR/9router.log" 2>&1 < /dev/null &
    echo $! > "$PID_DIR/9router.pid"
  fi
fi

if http_alive "http://127.0.0.1:8765/api/agents"; then
  echo "[ACTIS] Core já está ativo em http://127.0.0.1:8765"
  exit 0
fi

if pid_alive "$PID_DIR/core.pid"; then
  echo "[ACTIS] Processo do Core já existe; aguardando API..."
else
  echo "[ACTIS] Iniciando ACTIS Core local..."
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  export ACTIS_RUNTIME=android-termux
  export ACTIS_NODE_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin"
  nohup "$VENV/bin/actis-gen-web" \
    --host 127.0.0.1 \
    --port 8765 \
    --no-open \
    --embedded-scheduler \
    --env-file "$ENV_FILE" \
    >"$LOG_DIR/core.log" 2>&1 < /dev/null &
  echo $! > "$PID_DIR/core.pid"
fi

for _ in $(seq 1 40); do
  if http_alive "http://127.0.0.1:8765/api/agents"; then
    echo "[ACTIS] Core pronto em http://127.0.0.1:8765"
    exit 0
  fi
  sleep 0.5
done

echo "[ACTIS] Core não respondeu a tempo. Últimas linhas do log:" >&2
tail -n 40 "$LOG_DIR/core.log" 2>/dev/null || true
exit 1
