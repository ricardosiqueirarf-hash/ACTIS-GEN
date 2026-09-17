"""Executable HTTP connector runtime for authorized ACTIS GEN agents."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from meuharness.connectors import connector_visible_to_agent, list_connectors

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_ALLOWED_MODES = {"read", "write"}
_MAX_RESPONSE_BYTES = 256 * 1024


def normalize_operations(connector: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in connector.get("operations") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip().lower().replace("-", "_")
        method = str(raw.get("method") or "GET").strip().upper()
        path = str(raw.get("path") or "").strip()
        mode = str(raw.get("mode") or ("read" if method == "GET" else "write")).strip().lower()
        parts = urllib.parse.urlsplit(path)
        if (
            not _NAME_RE.fullmatch(name)
            or name in seen
            or method not in _ALLOWED_METHODS
            or mode not in _ALLOWED_MODES
            or not path.startswith("/")
            or parts.scheme
            or parts.netloc
        ):
            continue
        seen.add(name)
        result.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "mode": mode,
                "description": str(raw.get("description") or name).strip()[:300],
                "timeout_s": max(1, min(int(raw.get("timeout_s") or 15), 60)),
            }
        )
    return result


def validate_operations(operations: list[Any]) -> list[dict[str, Any]]:
    normalized = normalize_operations({"operations": operations})
    if len(normalized) != len(operations):
        raise ValueError("Uma ou mais operations do connector são inválidas ou duplicadas.")
    return normalized

def _env_values(env_file: str | None) -> dict[str, Any]:
    env_path = Path(env_file) if env_file else Path.cwd() / ".env"
    return {**(dotenv_values(env_path) if env_path.is_file() else {}), **os.environ}


def _headers(connector: dict[str, Any], env_file: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json, text/plain, */*", "User-Agent": "ACTIS-GEN/connector-runtime"}
    credential_ref = str(connector.get("credential_ref") or "").strip()
    token = str(_env_values(env_file).get(credential_ref) or "") if credential_ref else ""
    auth_type = str(connector.get("auth_type") or "none")
    if auth_type in {"bearer_env", "api_key_env"} and credential_ref and not token:
        raise RuntimeError(f"Credencial ausente para connector: {credential_ref}")
    if token and auth_type == "bearer_env":
        headers["Authorization"] = f"Bearer {token}"
    elif token and auth_type == "api_key_env":
        headers["X-API-Key"] = token
    return headers


def _json_object(raw: str, field: str) -> dict[str, Any]:
    if not str(raw or "").strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} precisa ser JSON válido.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} precisa ser um objeto JSON.")
    return value

def _build_url(base_url: str, path: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    remaining = dict(params)
    rendered = path
    for key in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path):
        if key not in remaining:
            raise ValueError(f"Parâmetro de path ausente: {key}")
        rendered = rendered.replace("{" + key + "}", urllib.parse.quote(str(remaining.pop(key)), safe=""))
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", rendered.lstrip("/"))
    if remaining:
        query = urllib.parse.urlencode(remaining, doseq=True)
        url += ("&" if "?" in url else "?") + query
    return url, remaining


def execute_operation(
    connector: dict[str, Any], operation: dict[str, Any], *,
    params_json: str = "{}", body_json: str = "{}",
    idempotency_key: str = "", env_file: str | None = None,
) -> str:
    params = _json_object(params_json, "params_json")
    body = _json_object(body_json, "body_json")
    url, _ = _build_url(str(connector.get("base_url") or ""), operation["path"], params)
    headers = _headers(connector, env_file)
    payload: bytes | None = None
    if operation["method"] in {"POST", "PUT", "PATCH"}:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    clean_key = str(idempotency_key or "").strip()
    if clean_key and operation["mode"] == "write":
        headers["Idempotency-Key"] = clean_key[:200]
    request = urllib.request.Request(url, data=payload, headers=headers, method=operation["method"])
    try:
        with urllib.request.urlopen(request, timeout=operation["timeout_s"]) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise RuntimeError("Resposta do connector excedeu 256 KiB.")
            text = raw.decode("utf-8", errors="replace")
            try:
                value: Any = json.loads(text) if text else None
            except json.JSONDecodeError:
                value = text
            return json.dumps(
                {"ok": True, "status": response.status, "data": value},
                ensure_ascii=False,
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(8192).decode("utf-8", errors="replace")
        raise RuntimeError(f"Connector HTTP {exc.code}: {raw[:1000]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Connector indisponível: {exc}") from None


def connector_runtime_catalog(agent_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for connector in list_connectors():
        if connector.get("kind") != "http" or not connector_visible_to_agent(connector, agent_id):
            continue
        operations = normalize_operations(connector)
        if not operations:
            continue
        result.append(
            {
                "id": connector.get("id"),
                "name": connector.get("name"),
                "operations": operations,
            }
        )
    return result


def _tool_name(connector_id: str, operation_name: str) -> str:
    safe_connector = re.sub(r"[^a-z0-9_]+", "_", connector_id.lower().replace("-", "_"))
    return f"connector_{safe_connector}_{operation_name}"[:120]


def _make_tool(connector: dict[str, Any], operation: dict[str, Any], env_file: str | None):
    def call(params_json: str = "{}", body_json: str = "{}", idempotency_key: str = "") -> str:
        return execute_operation(
            connector, operation, params_json=params_json, body_json=body_json,
            idempotency_key=idempotency_key, env_file=env_file,
        )
    call.__name__ = _tool_name(str(connector.get("id") or "http"), operation["name"])
    call.__qualname__ = call.__name__
    call.__doc__ = (
        f"Connector {connector.get('name')}: {operation['description']} "
        f"[{operation['method']} {operation['path']}; mode={operation['mode']}]. "
        "params_json preenche parâmetros de path {id} e query string. "
        "body_json envia JSON em POST/PUT/PATCH. Para writes, forneça idempotency_key quando possível."
    )
    return call


def build_connector_tools(
    agent_id: str, *, env_file: str | None = None, run_id: str | None = None
) -> list[object]:
    del run_id
    tools: list[object] = []
    for connector in list_connectors():
        if connector.get("kind") != "http" or not connector_visible_to_agent(connector, agent_id):
            continue
        for operation in normalize_operations(connector):
            tools.append(_make_tool(dict(connector), dict(operation), env_file))
    return tools
