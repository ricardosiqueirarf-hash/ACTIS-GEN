#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

REPO_URL="https://github.com/ricardosiqueirarf-hash/ACTIS-GEN.git"
BRANCH="${ACTIS_BRANCH:-feat/android-client}"
ROOT="${ACTIS_ROOT:-$HOME/ACTIS-GEN}"
STATE="${ACTIS_STATE_DIR:-$HOME/.actis-gen-termux}"
VENV="$STATE/venv"
ENV_FILE="$STATE/actis.env"
LOG_DIR="$STATE/logs"

mkdir -p "$STATE" "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/install.log") 2>&1

echo "[ACTIS] Preparando Core local no Termux..."

pkg update -y
pkg install -y git curl python nodejs-lts clang make pkg-config openssl libffi rust

if [ -d "$ROOT/.git" ]; then
  echo "[ACTIS] Atualizando código..."
  git -C "$ROOT" fetch --depth 1 origin "$BRANCH"
  git -C "$ROOT" checkout -B "$BRANCH" FETCH_HEAD
else
  echo "[ACTIS] Baixando ACTIS GEN..."
  rm -rf "$ROOT"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$ROOT"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "[ACTIS] Criando ambiente Python..."
  python -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -U setuptools wheel
python -m pip install -e "$ROOT"

# Ferramentas diretas do Core. Falhas aqui não impedem o servidor ACTIS de subir.
echo "[ACTIS] Instalando ferramentas Node/9Router..."
npm install -g 9router @modelcontextprotocol/server-filesystem mcp-shell-server || \
  echo "[ACTIS] AVISO: uma ferramenta Node opcional não instalou; o Core continuará disponível."

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
ACTIS_RUNTIME=android-termux
ACTIS_NODE_BIN=/data/data/com.termux/files/usr/bin
NINEROUTER_BASE_URL=http://127.0.0.1:20128/v1
NINEROUTER_API_KEY=sk_9router
NINEROUTER_MODEL=oc/big-pickle
NINEROUTER_TIMEOUT_SECONDS=60
EOF
fi

# Mantém a permissão de comandos externos habilitada para que o APK possa iniciar/parar o Core.
mkdir -p "$HOME/.termux"
touch "$HOME/.termux/termux.properties"
if grep -q '^allow-external-apps=' "$HOME/.termux/termux.properties"; then
  sed -i 's/^allow-external-apps=.*/allow-external-apps=true/' "$HOME/.termux/termux.properties"
else
  printf '\nallow-external-apps=true\n' >> "$HOME/.termux/termux.properties"
fi
command -v termux-reload-settings >/dev/null 2>&1 && termux-reload-settings || true

chmod +x "$ROOT/apps/android/termux/"*.sh

echo "[ACTIS] Instalação concluída. Iniciando serviços locais..."
"$ROOT/apps/android/termux/start-core.sh"

echo
echo "[ACTIS] Core local: http://127.0.0.1:8765"
echo "[ACTIS] 9Router local: http://127.0.0.1:20128"
echo "[ACTIS] Logs: $LOG_DIR"
