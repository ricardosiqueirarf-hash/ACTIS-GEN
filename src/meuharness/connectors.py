"""Persistent connector registry for ACTIS GEN."""
from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

from meuharness.agents import list_agents
from meuharness.channels import list_channels
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.storage import load_collection, save_collection

_COLLECTION = "connectors"
_DEFAULTS = [
    {
        "id": "nine-router", "name": "9Router", "kind": "nine_router",
        "description": "Gateway de modelos e providers do ACTIS GEN.",
        "base_url": "http://127.0.0.1:20128/v1", "auth_type": "api_key_env",
        "credential_ref": "NINEROUTER_API_KEY", "enabled": True,
        "agent_ids": ["*"], "capabilities": ["models", "completion", "providers"],
        "builtin": True,
    },
    {
        "id": "actis-agent-bus", "name": "ACTIS Agent Bus", "kind": "agent_bus",
        "description": "Comunicação interna persistente entre agentes sobre Canais.",
        "base_url": "actis://channels", "auth_type": "internal",
        "credential_ref": "", "enabled": True, "agent_ids": ["*"],
        "capabilities": ["discover_agents", "channels", "messages", "delegation"],
        "builtin": True,
    },
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_defaults() -> list[dict[str, Any]]:
    items = load_collection(_COLLECTION)
    known = {str(item.get("id") or "") for item in items}
    changed = False
    for default in _DEFAULTS:
        if default["id"] not in known:
            items.append({**default, "created_at": _now(), "updated_at": _now()})
            changed = True
    if changed:
        save_collection(_COLLECTION, items)
    return items

def list_connectors() -> list[dict[str, Any]]:
    return [dict(item) for item in _ensure_defaults()]


def get_connector(connector_id: str) -> dict[str, Any] | None:
    return next((dict(item) for item in _ensure_defaults() if item.get("id") == connector_id), None)


def save_connector(data: dict[str, Any]) -> dict[str, Any]:
    items = _ensure_defaults()
    connector_id = str(data.get("id") or "").strip() or f"connector-{uuid4().hex[:10]}"
    current = next((item for item in items if item.get("id") == connector_id), None)
    created_at = str((current or {}).get("created_at") or _now())
    protected = bool((current or {}).get("builtin"))
    item = {
        **(current or {}), **data, "id": connector_id,
        "name": str(data.get("name") or (current or {}).get("name") or connector_id).strip(),
        "kind": str((current or {}).get("kind") if protected else data.get("kind") or (current or {}).get("kind") or "http").strip(),
        "base_url": str((current or {}).get("base_url") if protected else data.get("base_url") or (current or {}).get("base_url") or "").strip(),
        "auth_type": str((current or {}).get("auth_type") if protected else data.get("auth_type") or (current or {}).get("auth_type") or "none").strip(),
        "credential_ref": str((current or {}).get("credential_ref") if protected else data.get("credential_ref") or (current or {}).get("credential_ref") or "").strip(),
        "enabled": bool(data.get("enabled", (current or {}).get("enabled", True))),
        "agent_ids": list(data.get("agent_ids") or (current or {}).get("agent_ids") or []),
        "capabilities": list(data.get("capabilities") or (current or {}).get("capabilities") or []),
        "operations": list(data.get("operations") if "operations" in data else (current or {}).get("operations") or []),
        "created_at": created_at, "updated_at": _now(),
    }
    if item["kind"] == "http":
        parts = urlsplit(item["base_url"])
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Connector HTTP exige base_url http(s) válida sem credenciais na URL.")
        from meuharness.connector_runtime import validate_operations
        item["operations"] = validate_operations(list(item.get("operations") or []))
    if current is None:
        items.append(item)
    else:
        items[items.index(current)] = item
    save_collection(_COLLECTION, items)
    return dict(item)


def delete_connector(connector_id: str) -> bool:
    items = _ensure_defaults()
    current = next((item for item in items if item.get("id") == connector_id), None)
    if not current or current.get("builtin"):
        return False
    save_collection(_COLLECTION, [item for item in items if item.get("id") != connector_id])
    return True


def connector_visible_to_agent(connector: dict[str, Any], agent_id: str) -> bool:
    allowed = {str(value) for value in connector.get("agent_ids") or []}
    return bool(connector.get("enabled", True)) and ("*" in allowed or agent_id in allowed)

def test_connector(connector_id: str, env_file: str | None = None) -> dict[str, Any]:
    item = get_connector(connector_id)
    if not item:
        raise ValueError("Connector não encontrado.")
    if not item.get("enabled", True):
        return {"ok": False, "status": "disabled", "connector_id": connector_id}
    kind = str(item.get("kind") or "")
    if kind == "nine_router":
        settings = NineRouterSettings.from_env(env_file)
        models = asyncio.run(NineRouterGateway(settings).list_models())
        return {"ok": True, "status": "connected", "connector_id": connector_id, "detail": f"{len(models)} modelos disponíveis"}
    if kind == "agent_bus":
        return {"ok": True, "status": "connected", "connector_id": connector_id, "detail": f"{len(list_agents())} agentes · {len(list_channels())} canais"}
    if kind != "http":
        return {"ok": False, "status": "unsupported", "connector_id": connector_id}
    headers = {"Accept": "application/json, text/plain, */*", "User-Agent": "ACTIS-GEN/connector-probe"}
    credential_ref = str(item.get("credential_ref") or "").strip()
    env_path = Path(env_file) if env_file else Path.cwd() / ".env"
    values = {**(dotenv_values(env_path) if env_path.is_file() else {}), **os.environ}
    token = str(values.get(credential_ref) or "") if credential_ref else ""
    if token and item.get("auth_type") == "bearer_env":
        headers["Authorization"] = f"Bearer {token}"
    elif token and item.get("auth_type") == "api_key_env":
        headers["X-API-Key"] = token
    req = urllib.request.Request(str(item.get("base_url") or ""), headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return {"ok": 200 <= response.status < 500, "status": "connected", "connector_id": connector_id, "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"ok": exc.code < 500, "status": "reachable", "connector_id": connector_id, "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": "unreachable", "connector_id": connector_id, "error": str(exc)}
