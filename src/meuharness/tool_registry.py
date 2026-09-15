"""ACTIS GEN tool catalog and canonical names."""

from __future__ import annotations

from typing import Any

TOOL_ALIASES = {
    "browser_harness": "harness_browser",
    "harness_browser": "harness_browser",
    "actis_admin": "actis_admin",
    "files": "harness_files",
    "harness_files": "harness_files",
    "terminal": "harness_terminal",
    "harness_terminal": "harness_terminal",
    "computer": "harness_computer",
    "computer_use": "harness_computer",
    "harness_computer": "harness_computer",
}

TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "harness_browser",
        "name": "Harness Browser",
        "icon": "🌐",
        "status": "ready",
        "kind": "MCP",
        "description": "Navegação web isolada via Browser Harness e Chrome headless gerenciado por agente.",
        "capabilities": ["navigate", "click", "type", "tabs", "screenshot", "upload"],
        "scopes": ["browser.read", "browser.interact"],
    },
    {
        "id": "harness_files",
        "name": "Harness Files",
        "icon": "📁",
        "status": "ready",
        "kind": "MCP",
        "description": "Arquivos locais via MCP Filesystem oficial, limitado ao diretório do usuário.",
        "capabilities": ["read", "write", "edit", "search", "move", "metadata"],
        "scopes": ["files.read", "files.write"],
    },
    {
        "id": "harness_terminal",
        "name": "Harness Terminal",
        "icon": "⌨",
        "status": "ready",
        "kind": "MCP",
        "description": "Terminal, processos e sessões locais via MCP Shell Server.",
        "capabilities": ["shell", "processes", "terminal", "history", "outputs"],
        "scopes": ["terminal.exec"],
    },
    {
        "id": "harness_computer",
        "name": "Harness Computer",
        "icon": "▣",
        "status": "ready",
        "kind": "MCP",
        "description": "Computer Use real: screenshot, mouse, teclado, apps e janelas via Zavora Computer Use MCP.",
        "capabilities": ["screenshot", "click", "mouse", "keyboard", "scroll", "apps", "windows"],
        "scopes": ["computer.view", "computer.control"],
    },
    {
        "id": "actis_admin",
        "name": "ACTIS Admin",
        "icon": "⚙",
        "status": "ready",
        "kind": "native",
        "description": "Plano de controle completo do ACTIS GEN para o General.",
        "capabilities": ["agents", "companies", "sectors", "projects", "context", "models", "runs", "tasks", "events", "approvals", "automations", "workflows", "conversations", "delegate"],
        "scopes": ["actis.admin"],
    },
]


def canonical_tool_id(tool_id: str) -> str:
    """Return the canonical ACTIS GEN tool id."""
    value = str(tool_id or "").strip()
    return TOOL_ALIASES.get(value, value)


def normalize_tool_ids(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize, deduplicate and preserve tool ordering."""
    result: list[str] = []
    for value in values or []:
        tool_id = canonical_tool_id(value)
        if tool_id and tool_id not in result:
            result.append(tool_id)
    return result


def list_tool_catalog() -> list[dict[str, Any]]:
    """Return a copy of the public tool catalog."""
    return [dict(item) for item in TOOL_CATALOG]


def scopes_for_tools(tool_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    selected = set(normalize_tool_ids(tool_ids))
    result: list[str] = []
    for item in TOOL_CATALOG:
        if item["id"] not in selected:
            continue
        for scope in item.get("scopes", []):
            if scope not in result:
                result.append(scope)
    return result


def normalize_scopes(values: list[str] | tuple[str, ...] | None) -> list[str]:
    valid = {scope for item in TOOL_CATALOG for scope in item.get("scopes", [])}
    result: list[str] = []
    for value in values or []:
        scope = str(value or "").strip()
        if scope in valid and scope not in result:
            result.append(scope)
    return result
