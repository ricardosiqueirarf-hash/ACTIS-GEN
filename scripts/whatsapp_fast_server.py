#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(os.environ.get("ACTIS_GEN_ROOT", Path.home() / "ACTIS-GEN"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from whatsapp_fast import CDP, clear_draft, draft, open_chat, send, status, whatsapp_target  # noqa: E402
from meuharness.browser_manager import ensure_browser  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("ACTIS_WA_FAST_PORT", "8767"))
BROWSER_ID = os.environ.get("ACTIS_BROWSER_AGENT_ID", "chatgpt-android")


class Controller:
    def __init__(self):
        self.lock = threading.Lock()
        self.cdp: CDP | None = None
        self.connect()

    def connect(self) -> None:
        if self.cdp:
            self.cdp.close()
        runtime = ensure_browser(BROWSER_ID)
        target = whatsapp_target(runtime.cdp_url)
        self.cdp = CDP(str(target["webSocketDebuggerUrl"]))

    def run(self, op: str, values: dict[str, str]) -> dict:
        with self.lock:
            started = time.perf_counter()
            for attempt in range(2):
                try:
                    assert self.cdp is not None
                    if op == "status": out = status(self.cdp)
                    elif op == "open": out = open_chat(self.cdp, values.get("chat", ""))
                    elif op == "draft": out = draft(self.cdp, values.get("chat", ""), values.get("text", ""))
                    elif op == "clear": out = clear_draft(self.cdp)
                    elif op == "send": out = send(self.cdp, values.get("chat", ""), values.get("text", ""), values.get("confirm") == "1")
                    else: raise ValueError(f"operação desconhecida: {op}")
                    out["server_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                    return out
                except Exception:
                    if attempt == 0:
                        try:
                            self.connect()
                            continue
                        except Exception:
                            pass
                    raise
            raise RuntimeError("falha inesperada")


CONTROLLER = Controller()


class Handler(BaseHTTPRequestHandler):
    server_version = "ACTIS-WA-Fast/1"

    def log_message(self, fmt, *args):
        return

    def _reply(self, code: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        op = self.path.strip("/") or "status"
        try:
            self._reply(200, CONTROLLER.run(op, {}))
        except Exception as exc:
            self._reply(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        op = self.path.split("?", 1)[0].strip("/")
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size).decode("utf-8", "replace")
        parsed = parse_qs(raw, keep_blank_values=True)
        values = {k: v[-1] if v else "" for k, v in parsed.items()}
        try:
            self._reply(200, CONTROLLER.run(op, values))
        except Exception as exc:
            self._reply(500, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever(poll_interval=0.1)
