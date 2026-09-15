"""Operational state instrumentation for ACTIS World."""
from __future__ import annotations

from meuharness.event_bus import emit_event
from meuharness.tasks import update_task_by_run


def set_run_activity(
    run_id: str,
    agent_id: str,
    activity: str,
    *,
    detail: str | None = None,
    tool: str | None = None,
) -> None:
    update_task_by_run(run_id, activity=activity, detail=detail, tool=tool)
    emit_event(
        "agent.state",
        run_id=run_id,
        agent_id=agent_id,
        state=activity,
        detail=detail,
        tool=tool,
    )