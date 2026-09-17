#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="${ACTIS_GEN_ROOT:-$HOME/ACTIS-GEN}"
VENV="${ACTIS_BROWSER_VENV:-$ROOT/.venv-android-browser}"

command -v chromium-browser >/dev/null 2>&1 || {
  echo "Chromium ausente. Instale no Termux antes de continuar." >&2
  exit 2
}

python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install --no-deps   'browser-harness==0.1.13'   'cdp-use==1.4.5'   'fetch-use==0.4.0'   'websockets==15.0.1'
"$VENV/bin/pip" install 'httpx>=0.28.1' 'typing-extensions>=4.12.2'

mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/scripts/chatgpt_browser_android.sh" "$HOME/.local/bin/chatgpt-browser"
TERMUX_BIN="$(dirname "$(command -v python3)")"
ln -sfn "$ROOT/scripts/chatgpt_browser_android.sh" "$TERMUX_BIN/chatgpt-browser"
chmod +x "$ROOT/scripts/chatgpt_wa_android.sh" "$ROOT/scripts/whatsapp_fast.py" "$ROOT/scripts/whatsapp_fast_server.py"
ln -sfn "$ROOT/scripts/chatgpt_wa_android.sh" "$HOME/.local/bin/chatgpt-wa"
ln -sfn "$ROOT/scripts/chatgpt_wa_android.sh" "$TERMUX_BIN/chatgpt-wa"
"$VENV/bin/browser-harness" recordings disable >/dev/null 2>&1 || true

echo "OK: use chatgpt-browser para controlar o Chromium Android persistente."
