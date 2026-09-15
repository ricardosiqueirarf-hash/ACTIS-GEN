"""Read-only ACTIS GEN tools available to every agent."""

from __future__ import annotations

import json

from meuharness.tool_registry import list_tool_catalog


def actis_list_tools() -> str:
    """Liste todas as tools disponíveis no ACTIS GEN e suas capacidades."""
    return json.dumps({"tools": list_tool_catalog()}, ensure_ascii=False)


def build_public_tools() -> list[object]:
    """Tools safe to expose to every agent without prior authorization."""
    return [actis_list_tools]
