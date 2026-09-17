#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

check() {
  local name="$1"
  local url="$2"
  if curl -sS --max-time 1 -o /dev/null "$url" >/dev/null 2>&1; then
    printf '%s: online\n' "$name"
  else
    printf '%s: offline\n' "$name"
  fi
}

check "ACTIS Core" "http://127.0.0.1:8765/api/agents"
check "9Router" "http://127.0.0.1:20128"
