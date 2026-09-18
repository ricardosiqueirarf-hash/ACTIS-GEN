"""ACTIS GEN tool catalog, skill sets and canonical names."""

from __future__ import annotations

from pathlib import Path
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
    "pdf": "pdf_toolkit",
    "pdf_toolkit": "pdf_toolkit",
    "excel": "skill_excel",
    "spreadsheet": "skill_excel",
    "xlsx": "skill_excel",
    "excel_spreadsheet": "skill_excel",
    "excel_toolkit": "skill_excel",
    "skill_excel": "skill_excel",
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
        "inheritable": False,
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
        "inheritable": False,
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
        "inheritable": False,
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
        "inheritable": False,
    },
    {
        "id": "skill_excel",
        "name": "Excel Spreadsheet",
        "icon": "▦",
        "status": "ready",
        "kind": "skill-set",
        "category": "office",
        "description": "Skill herdável para criar, ler, editar e validar planilhas Excel .xlsx com fórmulas, formatação, tabelas e gráficos.",
        "capabilities": [
            "create_workbook",
            "inspect_workbook",
            "edit_cells",
            "formulas",
            "formatting",
            "tables",
            "charts",
            "validate_workbook",
        ],
        "scopes": ["spreadsheet.read", "spreadsheet.write"],
        "inheritable": True,
        "skill_path": "skills/excel/SKILL.md",
    },
    {
        "id": "pdf_toolkit",
        "name": "PDF Toolkit",
        "icon": "PDF",
        "status": "ready",
        "kind": "native",
        "description": "Criação, inspeção, edição, merge e renderização verificável de PDFs locais.",
        "capabilities": ["inspect", "create", "edit", "merge", "render"],
        "scopes": ["pdf.read", "pdf.write"],
    },
    {
        "id": "actis_admin",
        "name": "ACTIS Admin",
        "icon": "⚙",
        "status": "ready",
        "kind": "native",
        "description": "Plano de controle completo do ACTIS GEN para qualquer agente autorizado.",
        "capabilities": [
            "features", "agents", "companies", "company_workspace", "sectors", "projects", "context", "memory", "project_memory", "models",
            "runs", "tasks", "events", "approvals", "automations", "workflows",
            "connectors", "connector_admin", "channels", "channel_admin", "agent_bus",
            "whatsapp_admin", "conversations", "delegate",
        ],
        "scopes": ["actis.admin"],
        "inheritable": False,
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


def get_tool_definition(tool_id: str) -> dict[str, Any] | None:
    canonical = canonical_tool_id(tool_id)
    item = next((item for item in TOOL_CATALOG if item["id"] == canonical), None)
    return dict(item) if item else None


def list_tool_catalog() -> list[dict[str, Any]]:
    """Return a copy of the public tool/skill catalog."""
    return [dict(item) for item in TOOL_CATALOG]


def inheritable_tool_ids(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return only tools explicitly marked safe to inherit during delegation."""
    selected = normalize_tool_ids(values)
    inheritable = {item["id"] for item in TOOL_CATALOG if item.get("inheritable") is True}
    return [tool_id for tool_id in selected if tool_id in inheritable]


def skill_instructions_for_tools(values: list[str] | tuple[str, ...] | None) -> str:
    """Load SKILL.md instructions for active skill-set tools."""
    selected = set(normalize_tool_ids(values))
    blocks: list[str] = []
    package_root = Path(__file__).resolve().parent
    for item in TOOL_CATALOG:
        if item["id"] not in selected or str(item.get("kind") or "").lower() != "skill-set":
            continue
        relative = str(item.get("skill_path") or "").strip()
        if not relative:
            continue
        path = package_root / relative
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            blocks.append(f"\n\nSKILL SET ATIVO — {item['name']}:\n{text}")
    return "".join(blocks)


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
