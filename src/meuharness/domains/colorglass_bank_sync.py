"""Safe local wrapper around the Unicred Bank Bridge.

This module exposes a deliberately tiny command surface to the Finance agent.
It never accepts arbitrary shell arguments and never moves money.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


BANK_BRIDGE_BIN = Path(
    os.getenv(
        "ACTIS_UNICRED_BANK_BRIDGE_BIN",
        str(Path.home() / "unicred-bank-bridge" / "bank-bridge"),
    )
)


def _bridge_env() -> dict[str, str]:
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env


def _safe_error(code: str, *, detail: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "status": code}
    if detail:
        payload["detail"] = detail[:240]
    return payload


def _parse_json_output(stdout: str) -> dict[str, Any]:
    text = str(stdout or "").strip()
    if not text:
        raise ValueError("empty_output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Defensive fallback for commands that may print a short preamble.
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("non_json_output")
        payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("unexpected_output_shape")
    return payload


def _run_json(command: str, *, timeout: int) -> tuple[int, dict[str, Any]]:
    if command not in {"status", "sync", "db-status"}:
        raise ValueError("unsupported_bank_bridge_command")
    if not BANK_BRIDGE_BIN.is_file():
        return 127, _safe_error("bridge_not_found")
    try:
        proc = subprocess.run(
            [str(BANK_BRIDGE_BIN), command, "--json"],
            cwd=str(BANK_BRIDGE_BIN.parent),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_bridge_env(),
        )
    except subprocess.TimeoutExpired:
        return 124, _safe_error("timeout")
    except OSError as exc:
        return 126, _safe_error("bridge_exec_error", detail=type(exc).__name__)
    try:
        payload = _parse_json_output(proc.stdout)
    except Exception:
        return proc.returncode, _safe_error("invalid_bridge_output")
    return proc.returncode, payload


def bank_sync_status() -> dict[str, Any]:
    code, status = _run_json("status", timeout=8)
    auth = str(status.get("authentication") or "")
    result = {
        "ok": code == 0 and bool(status),
        "authentication": auth or "unknown",
        "ready_for_sync": auth == "authenticated",
        "browser_available": auth != "browser_unavailable",
        "warsaw_active": bool(status.get("warsaw_active")),
        "url_policy": status.get("url_policy"),
        "read_only_capture": status.get("read_only_capture"),
    }
    _, db = _run_json("db-status", timeout=8)
    if db.get("last_sync") is not None:
        result["last_extrato_sync"] = db.get("last_sync")
    if db.get("last_dda_sync") is not None:
        result["last_dda_sync"] = db.get("last_dda_sync")
    result["dda_active_count"] = int(db.get("dda_total") or 0)
    return result


def prepare_bank_login() -> dict[str, Any]:
    """Prepare isolated bank browser and stop for human authentication."""
    current = bank_sync_status()
    auth = current.get("authentication")
    if auth == "authenticated":
        return {
            **current,
            "ok": True,
            "status": "ready",
            "human_action_required": False,
        }
    if auth == "auth_required":
        return {
            **current,
            "ok": True,
            "status": "auth_required",
            "human_action_required": True,
            "action": "Faça o login manualmente na janela isolada da Unicred.",
        }
    if auth != "browser_unavailable":
        return {
            **current,
            "ok": False,
            "status": "bank_session_unknown",
            "human_action_required": True,
        }
    if not BANK_BRIDGE_BIN.is_file():
        return _safe_error("bridge_not_found")
    try:
        proc = subprocess.run(
            [str(BANK_BRIDGE_BIN), "login"],
            cwd=str(BANK_BRIDGE_BIN.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            env=_bridge_env(),
        )
    except subprocess.TimeoutExpired:
        return _safe_error("login_launcher_timeout")
    except OSError as exc:
        return _safe_error("login_launcher_error", detail=type(exc).__name__)
    after = bank_sync_status()
    # Poll only for browser startup readiness. This never retries authentication.
    for _ in range(8):
        if after.get("authentication") != "browser_unavailable":
            break
        time.sleep(0.25)
        after = bank_sync_status()
    return {
        **after,
        "ok": proc.returncode == 0,
        "status": "ready" if after.get("ready_for_sync") else "auth_required",
        "human_action_required": not bool(after.get("ready_for_sync")),
        "action": (
            None
            if after.get("ready_for_sync")
            else "Faça o login manualmente na janela isolada da Unicred."
        ),
    }


def daily_bank_sync() -> dict[str, Any]:
    """Run the only allowed daily import: extrato + complete DDA snapshot."""
    before = bank_sync_status()
    auth = before.get("authentication")
    if auth == "browser_unavailable":
        prepared = prepare_bank_login()
        return {
            "ok": False,
            "complete": False,
            "status": "auth_required",
            "human_action_required": True,
            "prepared": prepared,
        }
    if auth != "authenticated":
        return {
            "ok": False,
            "complete": False,
            "status": "auth_required" if auth == "auth_required" else "not_ready",
            "human_action_required": True,
        }

    code, sync = _run_json("sync", timeout=600)
    if code != 0:
        return {
            "ok": False,
            "complete": False,
            "status": str(sync.get("error") or sync.get("status") or "sync_failed"),
            "bridge": sync,
        }

    extrato = sync.get("extrato") if isinstance(sync.get("extrato"), dict) else {}
    dda = sync.get("dda") if isinstance(sync.get("dda"), dict) else {}
    bridge_complete = (
        bool(sync.get("ok"))
        and not bool(sync.get("partial"))
        and bool(dda.get("ok"))
        and isinstance(extrato.get("fetched"), int)
        and isinstance(dda.get("fetched"), int)
    )

    _, db = _run_json("db-status", timeout=8)
    last_extrato = db.get("last_sync") if isinstance(db.get("last_sync"), dict) else {}
    last_dda = db.get("last_dda_sync") if isinstance(db.get("last_dda_sync"), dict) else {}
    persisted_ok = (
        str(last_extrato.get("status") or "") == "ok"
        and str(last_dda.get("status") or "") == "ok"
    )
    complete = bool(bridge_complete and persisted_ok)

    return {
        "ok": complete,
        "complete": complete,
        "status": "complete" if complete else "partial_or_unverified",
        "read_only": True,
        "extrato": {
            "fetched": int(extrato.get("fetched") or 0),
            "new": int(extrato.get("new") or 0),
            "existing": int(extrato.get("existing") or 0),
            "persisted_status": last_extrato.get("status"),
        },
        "dda": {
            "fetched": int(dda.get("fetched") or 0),
            "new": int(dda.get("new") or 0),
            "existing": int(dda.get("existing") or 0),
            "persisted_status": last_dda.get("status"),
            "active_count": int(db.get("dda_total") or 0),
            "error": dda.get("error"),
        },
        "partial": not complete,
    }
