"""Safe ACTIS GEN tools available to every agent."""

from __future__ import annotations

import json

from meuharness.tool_registry import list_tool_catalog


def actis_list_tools() -> str:
    """Liste todas as tools disponíveis no ACTIS GEN e suas capacidades."""
    return json.dumps({"tools": list_tool_catalog()}, ensure_ascii=False)


def build_public_tools(*, agent_id: str = "", run_id: str | None = None, env_file: str | None = None) -> list[object]:
    """Safe baseline tools plus the isolated runtime of the agent's own company."""
    tools: list[object] = [actis_list_tools]
    if agent_id:
        from meuharness.company_bus import build_company_tools
        tools.extend(build_company_tools(agent_id, run_id=run_id, env_file=env_file))
    return tools
