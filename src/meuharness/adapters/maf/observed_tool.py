"""MAF tool wrapper that exposes live tool activity to ACTIS World."""
from __future__ import annotations

import functools
import inspect
import json
import threading
from typing import Any

from agent_framework import MCPStdioTool

from meuharness.durable_runs import append_checkpoint
from meuharness.event_bus import emit_event
from meuharness.observability import set_run_activity
from meuharness.tasks import update_work_by_run

_TRACE_LOCK = threading.RLock()
_RUN_TOOL_TRACE: dict[str, list[dict[str, Any]]] = {}


def clear_run_tool_trace(run_id: str) -> None:
    with _TRACE_LOCK:
        _RUN_TOOL_TRACE.pop(run_id, None)


def get_run_tool_trace(run_id: str) -> list[dict[str, Any]]:
    with _TRACE_LOCK:
        return [dict(item) for item in _RUN_TOOL_TRACE.get(run_id, [])]


def _record_tool(run_id: str | None, tool_name: str, ok: bool) -> None:
    if not run_id:
        return
    with _TRACE_LOCK:
        _RUN_TOOL_TRACE.setdefault(run_id, []).append({"tool": tool_name, "ok": ok})


def record_tool_result(run_id: str | None, tool_name: str, ok: bool) -> None:
    """Record evidence from native/runtime tools in the same trace used by MCP tools."""
    _record_tool(run_id, tool_name, ok)


def observe_native_tool(tool: Any, *, run_id: str | None, agent_id: str | None) -> Any:
    """Wrap a native Python tool with the same live activity/events used by MCP tools."""
    if not inspect.isfunction(tool):
        return tool
    tool_name = str(getattr(tool, "__name__", "native_tool"))
    signature = inspect.signature(tool)

    def started() -> int:
        before = len(get_run_tool_trace(run_id)) if run_id else 0
        if run_id and agent_id:
            set_run_activity(run_id, agent_id, "tool", detail=f"Executando {tool_name}", tool=tool_name)
            emit_event("tool.started", run_id=run_id, agent_id=agent_id, tool=tool_name)
        return before

    def finished(before: int, *, failed: bool, call_args: tuple[Any, ...] = (), call_kwargs: dict[str, Any] | None = None, result: Any = None, error: str = "") -> None:
        if run_id and len(get_run_tool_trace(run_id)) == before:
            _record_tool(run_id, tool_name, not failed)
        if not (run_id and agent_id):
            return
        diag_payload: dict[str, Any] = {}
        if tool_name.startswith("browser_") or tool_name.startswith("colorglass_quote_"):
            safe_kwargs = {}
            for key, value in dict(call_kwargs or {}).items():
                safe_kwargs[key] = "***" if any(token in key.lower() for token in ("pass", "senha", "secret", "token")) else str(value)[:500]
            diag_payload = {"args": [str(x)[:500] for x in call_args], "kwargs": safe_kwargs}
            if error:
                diag_payload["error"] = error[:500]
            elif result is not None:
                text = str(result)
                diag_payload["result"] = text[:1000]
        append_checkpoint(
            run_id, agent_id, "tool",
            status="failed" if failed else "completed",
            detail=tool_name,
            evidence=f"{tool_name}: {'falhou' if failed else 'concluída'}",
            payload=diag_payload,
        )
        if failed:
            update_work_by_run(run_id, phase="blocked", detail=f"Falha em {tool_name}", evidence=f"{tool_name}: falhou")
            emit_event("tool.failed", run_id=run_id, agent_id=agent_id, tool=tool_name)
            set_run_activity(run_id, agent_id, "blocked", detail=f"Falha em {tool_name}", tool=tool_name)
        else:
            update_work_by_run(run_id, phase="acting", detail=f"{tool_name} concluída", evidence=f"{tool_name}: concluída")
            emit_event("tool.completed", run_id=run_id, agent_id=agent_id, tool=tool_name)
            set_run_activity(run_id, agent_id, "thinking", detail="Processando resultado da ferramenta", tool=None)

    def result_failed(result: Any, before: int) -> bool:
        if run_id and any(not item["ok"] for item in get_run_tool_trace(run_id)[before:] if item["tool"] == tool_name):
            return True
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                return False
        return isinstance(result, dict) and (
            result.get("ok") is False or result.get("status") in {"failed", "blocked", "planned", "cancelled", "interrupted", "uncertain", "dead", "error", "auth_required", "order_not_found", "create_failed", "catalog_match_failed", "network_error", "verify_failed", "conflict", "calculation_failed", "save_failed"}
        )

    if inspect.iscoroutinefunction(tool):
        @functools.wraps(tool)
        async def async_wrapped(*args: Any, **kwargs: Any):
            before = started()
            try:
                result = await tool(*args, **kwargs)
            except Exception as exc:
                finished(before, failed=True, call_args=args, call_kwargs=kwargs, error=str(exc))
                raise
            finished(before, failed=result_failed(result, before), call_args=args, call_kwargs=kwargs, result=result)
            return result
        async_wrapped.__signature__ = signature
        return async_wrapped

    @functools.wraps(tool)
    def wrapped(*args: Any, **kwargs: Any):
        before = started()
        try:
            result = tool(*args, **kwargs)
        except Exception as exc:
            finished(before, failed=True, call_args=args, call_kwargs=kwargs, error=str(exc))
            raise
        finished(before, failed=result_failed(result, before), call_args=args, call_kwargs=kwargs, result=result)
        return result
    wrapped.__signature__ = signature
    return wrapped


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
            _record_tool(run_id, tool_name, False)
            if run_id and agent_id:
                append_checkpoint(run_id, agent_id, "tool", status="failed", detail=tool_name, evidence=f"{tool_name}: falhou")
                update_work_by_run(run_id, phase="blocked", detail=f"Falha em {tool_name}", evidence=f"{tool_name}: falhou")
                emit_event("tool.failed", run_id=run_id, agent_id=agent_id, tool=tool_name)
                set_run_activity(run_id, agent_id, "blocked", detail=f"Falha em {tool_name}", tool=tool_name)
            raise
        _record_tool(run_id, tool_name, True)
        if run_id and agent_id:
            append_checkpoint(run_id, agent_id, "tool", status="completed", detail=tool_name, evidence=f"{tool_name}: concluída")
            update_work_by_run(run_id, phase="acting", detail=f"{tool_name} concluída", evidence=f"{tool_name}: concluída")
            emit_event("tool.completed", run_id=run_id, agent_id=agent_id, tool=tool_name)
            set_run_activity(run_id, agent_id, "thinking", detail="Processando resultado da ferramenta", tool=None)
        return result
