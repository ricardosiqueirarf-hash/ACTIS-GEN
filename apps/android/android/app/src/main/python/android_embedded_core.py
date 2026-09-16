"""Embedded ACTIS Core entrypoint for Android/Chaquopy."""
from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer

_SERVER: ThreadingHTTPServer | None = None
_LOCK = threading.Lock()


def start(files_dir: str) -> str:
    """Start ACTIS Core on localhost:8765 and block this background thread."""
    global _SERVER
    with _LOCK:
        if _SERVER is not None:
            return "already-running"

        # Set the Android app-private directory as the ACTIS home before importing
        # modules which compute Path.home()-relative storage paths.
        os.environ["HOME"] = str(files_dir)
        os.environ["ACTIS_RUNTIME"] = "android-embedded"
        os.environ.setdefault("ACTIS_CORS_ORIGINS", "https://localhost,http://localhost,capacitor://localhost")

        from meuharness.agents import cancel_stale_running_runs
        from meuharness.automation_scheduler import start_scheduler
        from meuharness.web import Handler

        Handler.env_file = None
        cancel_stale_running_runs()
        _SERVER = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
        start_scheduler("http://127.0.0.1:8765", env_file=None)

    try:
        _SERVER.serve_forever(poll_interval=0.25)
    finally:
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
