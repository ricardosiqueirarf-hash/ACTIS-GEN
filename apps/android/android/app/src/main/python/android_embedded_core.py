"""Embedded ACTIS Core entrypoint for Android/Chaquopy."""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer

_SERVER: ThreadingHTTPServer | None = None
_LOCK = threading.Lock()
_SCHEDULER_STOP = None


def start(files_dir: str) -> str:
    """Start ACTIS Core on localhost:8765 and block this background thread."""
    global _SERVER, _SCHEDULER_STOP
    with _LOCK:
        if _SERVER is not None:
            return "already-running"

        # Must happen before importing ACTIS storage modules: Path.home() is used
        # to derive the canonical SQLite location.
        os.environ["HOME"] = str(files_dir)
        os.environ["ACTIS_RUNTIME"] = "android-embedded"
        os.environ.setdefault("ACTIS_CORS_ORIGINS", "https://localhost,http://localhost,capacitor://localhost")

        from meuharness.android_scheduler import start_android_scheduler
        from meuharness.web_android import make_server

        _SERVER = make_server("127.0.0.1", 8765)
        _SCHEDULER_STOP, _thread = start_android_scheduler()

    try:
        _SERVER.serve_forever(poll_interval=0.25)
    finally:
        if _SCHEDULER_STOP is not None:
            _SCHEDULER_STOP.set()
        with _LOCK:
            if _SERVER is not None:
                _SERVER.server_close()
            _SERVER = None
    return "stopped"


def stop() -> str:
    global _SERVER
    with _LOCK:
        server = _SERVER
    if server is None:
        return "not-running"
    server.shutdown()
    return "stopping"
