#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="${ACTIS_GEN_ROOT:-$HOME/ACTIS-GEN}"
VENV="${ACTIS_BROWSER_VENV:-$ROOT/.venv-android-browser}"
BROWSER_ID="${ACTIS_BROWSER_AGENT_ID:-chatgpt-android}"
PYTHON_BIN="$VENV/bin/python"
HARNESS_BIN="$VENV/bin/browser-harness"

if [ ! -x "$HARNESS_BIN" ]; then
  echo "Browser Harness Android nao instalado. Rode: $ROOT/scripts/bootstrap_chatgpt_browser_android.sh" >&2
  exit 2
fi

CDP_URL=$(PYTHONPATH="$ROOT/src" "$PYTHON_BIN" - <<PY2
from meuharness.browser_manager import ensure_browser
print(ensure_browser("$BROWSER_ID").cdp_url)
PY2
)

export BU_CDP_URL="$CDP_URL"
export BU_NAME="${BU_NAME:-$BROWSER_ID}"
export BH_TAB_MARKER="${BH_TAB_MARKER:-0}"
exec "$HARNESS_BIN" "$@"
