"""Managed isolated Chrome runtimes for ACTIS Browser Harness agents."""
from __future__ import annotations

import fcntl
import json
import logging
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
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserRuntime:
    agent_id: str
    profile_dir: str
    port: int
    pid: int
    cdp_url: str
    reused: bool = False
    mode: str = "headless"
    xephyr_pid: int = 0
    display: str = ""
    last_url: str = "about:blank"


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
            mode=str(data.get("mode") or "headless"),
            xephyr_pid=int(data.get("xephyr_pid") or 0),
            display=str(data.get("display") or ""),
            last_url=str(data.get("last_url") or "about:blank"),
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
                    mode="headless",
                    last_url="about:blank",
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


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _browser_current_url(runtime: BrowserRuntime) -> str:
    try:
        with urlopen(runtime.cdp_url + "/json/list", timeout=1.5) as response:
            pages = json.load(response)
        for page in pages if isinstance(pages, list) else []:
            if page.get("type") == "page" and page.get("url"):
                return str(page["url"])
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        pass
    return runtime.last_url or "about:blank"


def _stop_harness_daemon(profile: Path) -> None:
    pid_file = profile / "harness-runtime" / "bu.pid"
    if not pid_file.is_file():
        return
    try:
        raw = pid_file.read_text().strip()
        data = json.loads(raw) if raw.startswith("{") else raw
        pid = int(data.get("pid") if isinstance(data, dict) else data)
        if _process_alive(pid):
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and _process_alive(pid):
                time.sleep(0.05)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        pid_file.unlink(missing_ok=True)
        (profile / "harness-runtime" / "bu.sock").unlink(missing_ok=True)
    except OSError:
        pass


def _stop_runtime_processes(runtime: BrowserRuntime) -> None:
    profile = Path(runtime.profile_dir)
    _stop_harness_daemon(profile)
    if _process_alive(runtime.pid):
        try:
            os.kill(runtime.pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and _cdp_healthy(runtime.port):
            time.sleep(0.08)
    if runtime.xephyr_pid and _process_alive(runtime.xephyr_pid):
        try:
            os.kill(runtime.xephyr_pid, signal.SIGTERM)
        except OSError:
            pass


def _hypr_env() -> dict[str, str]:
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = "/run/user/1000"
    env["XAUTHORITY"] = "/dev/null"
    xwayland = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
            if not cmd.startswith("Xwayland "):
                continue
            values = (proc / "environ").read_bytes().split(b"\0")
            row = {k.decode(errors="ignore"): v.decode(errors="ignore") for item in values if b"=" in item for k, v in [item.split(b"=", 1)]}
        except (OSError, PermissionError):
            continue
        if row.get("HYPRLAND_INSTANCE_SIGNATURE") and row.get("DISPLAY"):
            xwayland.append(row)
    if xwayland:
        # Prefer the highest active Xwayland display; stale sessions may remain in /proc.
        row = max(xwayland, key=lambda item: int(str(item.get("DISPLAY", ":0")).lstrip(":") or 0))
        env.update({k: row[k] for k in ("DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR") if row.get(k)})
    return env


def _choose_x_display() -> int:
    for number in range(90, 120):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    raise RuntimeError("Nenhum display Xephyr livre disponível.")


def _move_xephyr_to_special_workspace(pid: int, env: dict[str, str]) -> None:
    if not shutil.which("hyprctl"):
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(["hyprctl", "clients", "-j"], env=env, capture_output=True, text=True, timeout=1, check=False)
            clients = json.loads(result.stdout or "[]")
            match = next((c for c in clients if int(c.get("pid") or 0) == pid), None)
            if match and match.get("address"):
                subprocess.run(["hyprctl", "dispatch", "movetoworkspacesilent", f"special:actis-browser,address:{match['address']}"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1, check=False)
                return
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            pass
        time.sleep(0.08)


def _start_verification_runtime(agent_id: str, profile: Path, port: int, url: str) -> BrowserRuntime:
    parent_env = _hypr_env()
    if not parent_env.get("DISPLAY"):
        raise RuntimeError("Sessão gráfica do usuário não encontrada para o modo de verificação.")
    display_no = _choose_x_display()
    display = f":{display_no}"
    xephyr_log = (profile / "xephyr.log").open("ab", buffering=0)
    xephyr = subprocess.Popen(["Xephyr", display, "-screen", "1440x1000", "-resizeable", "-nolisten", "tcp", "-noreset", "-title", f"ACTIS Browser · {agent_id}"], env=parent_env, stdin=subprocess.DEVNULL, stdout=xephyr_log, stderr=xephyr_log, start_new_session=True, close_fds=True)
    xephyr_log.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not Path(f"/tmp/.X11-unix/X{display_no}").exists():
        if xephyr.poll() is not None:
            raise RuntimeError("Xephyr encerrou antes de abrir o display de verificação.")
        time.sleep(0.08)
    _move_xephyr_to_special_workspace(xephyr.pid, parent_env)
    chrome_env = dict(parent_env)
    chrome_env["DISPLAY"] = display
    log = (profile / "chrome.log").open("ab", buffering=0)
    command = [_chrome_binary(), "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage", "--password-store=basic", "--window-size=1440,1000", url or "about:blank"]
    process = subprocess.Popen(command, env=chrome_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
    log.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _cdp_healthy(port):
            runtime = BrowserRuntime(agent_id=agent_id, profile_dir=str(profile), port=port, pid=process.pid, cdp_url=f"http://127.0.0.1:{port}", mode="verification", xephyr_pid=xephyr.pid, display=display, last_url=url or "about:blank")
            _write_runtime(profile, runtime)
            return runtime
        if process.poll() is not None:
            break
        time.sleep(0.12)
    try:
        process.terminate()
        xephyr.terminate()
    except ProcessLookupError:
        pass
    raise RuntimeError("Chrome gráfico de verificação não abriu o CDP.")


def _start_headless_runtime(agent_id: str, profile: Path, port: int, url: str) -> BrowserRuntime:
    log = (profile / "chrome.log").open("ab", buffering=0)
    command = [_chrome_binary(), "--headless=new", "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage", "--password-store=basic", "--window-size=1440,1000", url or "about:blank"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
    log.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _cdp_healthy(port):
            runtime = BrowserRuntime(agent_id=agent_id, profile_dir=str(profile), port=port, pid=process.pid, cdp_url=f"http://127.0.0.1:{port}", mode="headless", last_url=url or "about:blank")
            _write_runtime(profile, runtime)
            return runtime
        if process.poll() is not None:
            break
        time.sleep(0.12)
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    raise RuntimeError("Chrome headless não reabriu o CDP.")


def switch_browser_mode(agent_id: str, mode: str) -> BrowserRuntime:
    """Switch one agent browser between headless automation and headed verification."""
    mode = str(mode or "").strip().lower()
    if mode not in {"headless", "verification"}:
        raise ValueError("Modo de browser inválido.")
    safe_id = _safe_id(agent_id)
    profile = BROWSER_DIR / safe_id
    profile.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        runtime = _load_runtime(profile, safe_id)
        if runtime and runtime.mode == mode:
            return runtime
        if runtime:
            current_url = _browser_current_url(runtime)
            port = runtime.port
            _stop_runtime_processes(runtime)
        else:
            current_url = "about:blank"
            port = _choose_port(safe_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _port_free(port):
            time.sleep(0.08)
        try:
            if mode == "verification":
                return _start_verification_runtime(safe_id, profile, port, current_url)
            return _start_headless_runtime(safe_id, profile, port, current_url)
        except Exception:
            # Never strand the agent browser if a mode switch fails.
            if mode == "verification":
                try:
                    _start_headless_runtime(safe_id, profile, port, current_url)
                except (OSError, RuntimeError, ValueError) as restore_exc:
                    LOGGER.error("Failed to restore headless browser after verification switch failure: %s", restore_exc)
            raise
