"""MAF tool wrapper that exposes live tool activity to ACTIS World."""
from __future__ import annotations

from typing import Any

from agent_framework import MCPStdioTool

from meuharness.event_bus import emit_event
from meuharness.observability import set_run_activity


class ObservedMCPStdioTool(MCPStdioTool):
    def __init__(self, *args: Any, actis_run_id: str | None = None, actis_agent_id: str | None = None, **kwargs: Any) -> None:
        self.actis_run_id = actis_run_id
        self.actis_agent_id = actis_agent_id
        super().__init__(*args, **kwargs)

    async def call_tool(self, tool_name: str, **kwargs: Any):
        run_id, agent_id = self.actis_run_id, self.actis_agent_id
        if run_id and agent_id:
            set_run_activity(run_id, agent_id, "tool", detail=f"Executando {tool_name}", tool=tool_name)
            emit_event("tool.started", run_id=run_id, agent_id=agent_id, tool=tool_name)
        try:
            result = await super().call_tool(tool_name, **kwargs)
        except Exception:
            if run_id and agent_id:
                emit_event("tool.failed", run_id=run_id, agent_id=agent_id, tool=tool_name)
                set_run_activity(run_id, agent_id, "blocked", detail=f"Falha em {tool_name}", tool=tool_name)
            raise
        if run_id and agent_id:
            emit_event("tool.completed", run_id=run_id, agent_id=agent_id, tool=tool_name)
            set_run_activity(run_id, agent_id, "thinking", detail="Processando resultado da ferramenta", tool=None)
        return result