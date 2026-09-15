"""First-class operational tasks projected into ACTIS World."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.event_bus import emit_event
from meuharness.storage import load_collection, save_collection

COLLECTION = "tasks"

def _now() -> str:
    return datetime.now(UTC).isoformat()

def _read() -> list[dict[str, Any]]:
    return load_collection(COLLECTION)

def _write(items: list[dict[str, Any]]) -> None:
    save_collection(COLLECTION, items)

def list_tasks(*, active_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    items = sorted(_read(), key=lambda x: x.get("updated_at", ""), reverse=True)
    if active_only:
        items = [x for x in items if x.get("status") in {"active", "waiting", "blocked"}]
    return items[:max(1, min(int(limit), 1000))]

def get_task(task_id: str) -> dict[str, Any] | None:
    return next((x for x in _read() if x.get("id") == task_id), None)

def get_task_by_run(run_id: str) -> dict[str, Any] | None:
    return next((x for x in _read() if x.get("run_id") == run_id), None)

def create_task_for_run(run: dict[str, Any]) -> dict[str, Any]:
    if not run.get("id") or not run.get("agent_id"):
        return {"id": "", "run_id": run.get("id"), "agent_id": run.get("agent_id"), "status": "active", "activity": "thinking", "title": "Tarefa"}
    items = _read()
    existing = next((x for x in items if x.get("run_id") == run.get("id")), None)
    if existing:
        return existing
    prompt = str(run.get("prompt") or "").strip()
    title = prompt.replace("\n", " ")[:72] + ("…" if len(prompt.replace("\n", " ")) > 72 else "")
    now = _now()
    task = {
        "id": uuid4().hex, "run_id": run.get("id"), "agent_id": run.get("agent_id"),
        "parent_agent_id": run.get("parent_agent_id"), "source": run.get("source") or "user",
        "title": title or "Tarefa", "prompt": prompt, "status": "active",
        "activity": "thinking", "tool": None, "detail": "Processando solicitação",
        "created_at": now, "updated_at": now, "finished_at": None,
    }
    items.append(task); _write(items)
    emit_event("task.created", task_id=task["id"], run_id=task["run_id"], agent_id=task["agent_id"], parent_agent_id=task.get("parent_agent_id"), source=task.get("source"), title=task.get("title"))
    return task

def update_task_by_run(run_id: str, **changes: Any) -> dict[str, Any] | None:
    items = _read()
    for item in items:
        if item.get("run_id") != run_id:
            continue
        for key, value in changes.items():
            if value is not None or key in {"tool", "detail", "finished_at"}:
                item[key] = value
        item["updated_at"] = _now()
        _write(items)
        return dict(item)
    return None
