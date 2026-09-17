#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ROOT="${ACTIS_GEN_ROOT:-$HOME/ACTIS-GEN}"
VENV="${ACTIS_BROWSER_VENV:-$ROOT/.venv-android-browser}"
PYTHON_BIN="$VENV/bin/python"
PORT="${ACTIS_WA_FAST_PORT:-8767}"
BASE="http://127.0.0.1:$PORT"
LOG="$HOME/.local/share/actis-gen/whatsapp-fast.log"
PIDFILE="$HOME/.local/share/actis-gen/whatsapp-fast.pid"
mkdir -p "$(dirname "$LOG")"

start_server() {
  if ! curl -fsS --max-time 0.3 "$BASE/status" >/dev/null 2>&1; then
    nohup "$PYTHON_BIN" "$ROOT/scripts/whatsapp_fast_server.py" >>"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -fsS --max-time 0.3 "$BASE/status" >/dev/null 2>&1 && return 0
      sleep 0.05
    done
    return 1
  fi
}

start_server || exec "$PYTHON_BIN" "$ROOT/scripts/whatsapp_fast.py" "$@"
cmd="${1:-status}"
case "$cmd" in
  status) curl -fsS "$BASE/status" ;;
  open) [ $# -ge 2 ] || { echo 'uso: chatgpt-wa open CHAT' >&2; exit 2; }; curl -fsS -X POST --data-urlencode "chat=$2" "$BASE/open" ;;
  draft) [ $# -ge 3 ] || { echo 'uso: chatgpt-wa draft CHAT TEXTO' >&2; exit 2; }; curl -fsS -X POST --data-urlencode "chat=$2" --data-urlencode "text=$3" "$BASE/draft" ;;
  clear) curl -fsS -X POST "$BASE/clear" ;;
  send)
    [ $# -ge 3 ] || { echo 'uso: chatgpt-wa send CHAT TEXTO --confirm-send' >&2; exit 2; }
    [ "${4:-}" = "--confirm-send" ] || { echo 'envio bloqueado: falta --confirm-send' >&2; exit 3; }
    curl -fsS -X POST --data-urlencode "chat=$2" --data-urlencode "text=$3" --data "confirm=1" "$BASE/send"
    ;;
  *) echo "comando inválido: $cmd" >&2; exit 2 ;;
esac
echo
