"""Managed isolated Chrome runtimes for ACTIS Browser Harness agents."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

DATA_DIR = Path.home() / ".local" / "share" / "actis-gen"
BROWSER_DIR = DATA_DIR / "browser-profiles"
LOCK_FILE = DATA_DIR / "browser-manager.lock"
PORT_MIN = 9222
PORT_MAX = 9822


@dataclass(frozen=True)
class BrowserRuntime:
    agent_id: str
    profile_dir: str
    port: int
    pid: int
    cdp_url: str
    reused: bool = False


def _safe_id(agent_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(agent_id or "shared")).strip("-.")
    return value[:80] or "shared"


def _runtime_file(profile: Path) -> Path:
    return profile / "runtime.json"


def _cdp_healthy(port: int, timeout: float = 0.35) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _preferred_port(agent_id: str) -> int:
    span = PORT_MAX - PORT_MIN + 1
    digest = int.from_bytes(sha256(agent_id.encode()).digest()[:4], "big")
    return PORT_MIN + (digest % span)


def _choose_port(agent_id: str) -> int:
    preferred = _preferred_port(agent_id)
    span = PORT_MAX - PORT_MIN + 1
    for offset in range(span):
        port = PORT_MIN + ((preferred - PORT_MIN + offset) % span)
        if _port_free(port):
            return port
    raise RuntimeError("Nenhuma porta CDP local disponível para o Browser Harness.")


def _chrome_binary() -> str:
    explicit = os.environ.get("ACTIS_BROWSER_CHROME")
    if explicit and Path(explicit).is_file():
        return explicit
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Chrome/Chromium não encontrado para o Browser Harness gerenciado.")


def _stop_stale_runtime(profile: Path) -> None:
    path = _runtime_file(profile)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text())
        pid = int(data.get("pid") or 0)
        if pid <= 0:
            return
        cmdline = Path(f"/proc/{pid}/cmdline")
        if not cmdline.is_file():
            return
        command = cmdline.read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        if f"--user-data-dir={profile}" in command:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.15)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def _load_runtime(profile: Path, agent_id: str) -> BrowserRuntime | None:
    path = _runtime_file(profile)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        runtime = BrowserRuntime(
            agent_id=agent_id,
            profile_dir=str(profile),
            port=int(data["port"]),
            pid=int(data["pid"]),
            cdp_url=f"http://127.0.0.1:{int(data['port'])}",
            reused=True,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return runtime if _cdp_healthy(runtime.port) else None


def _write_runtime(profile: Path, runtime: BrowserRuntime) -> None:
    payload = asdict(runtime)
    payload["reused"] = False
    _runtime_file(profile).write_text(json.dumps(payload, indent=2) + "\n")


def ensure_browser(agent_id: str) -> BrowserRuntime:
    """Return a healthy per-agent headless Chrome, starting it when necessary."""
    safe_id = _safe_id(agent_id)
    profile = BROWSER_DIR / safe_id
    profile.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = _load_runtime(profile, safe_id)
        if existing:
            return existing
        _stop_stale_runtime(profile)
        port = _choose_port(safe_id)
        log_path = profile / "chrome.log"
        log = log_path.open("ab", buffering=0)
        command = [
            _chrome_binary(),
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--password-store=basic",
            "--window-size=1440,1000",
            "about:blank",
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
        log.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _cdp_healthy(port):
                runtime = BrowserRuntime(
                    agent_id=safe_id,
                    profile_dir=str(profile),
                    port=port,
                    pid=process.pid,
                    cdp_url=f"http://127.0.0.1:{port}",
                )
                _write_runtime(profile, runtime)
                return runtime
            if process.poll() is not None:
                break
            time.sleep(0.12)
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-8:])
        except OSError:
            pass
        raise RuntimeError(f"Chrome gerenciado não abriu o CDP para {safe_id}. {tail}".strip())
