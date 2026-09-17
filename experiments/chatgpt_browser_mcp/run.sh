#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/home/lowkanta/.local/share/uv/tools/browser-harness/bin/python"
exec "$PYTHON" "$HERE/server.py"
