#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="${ACTIS_GEN_ROOT:-$HOME/ACTIS-GEN}"
ACTIS_BROWSER_AGENT_ID=chatgpt-android-smoke "$ROOT/scripts/chatgpt_browser_android.sh" <<'PY2'
ensure_real_tab()
goto_url("data:text/html,<title>ACTIS Android Harness</title><button id=b>OK</button><input id=i>")
wait(0.3)
assert page_info()["title"] == "ACTIS Android Harness"
assert js("document.querySelector('#b').innerText") == "OK"
fill_input("#i", "teste-android")
assert js("document.querySelector('#i').value") == "teste-android"
print("ANDROID_BROWSER_HARNESS_OK")
print(page_info())
PY2
