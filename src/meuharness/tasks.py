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


def _work_template(prompt: str) -> dict[str, Any]:
    objective = " ".join(str(prompt or "").split()).strip()
    if len(objective) > 220:
        objective = objective[:217] + "…"
    return {
        "objective": objective or "Concluir a solicitação do usuário",
        "state": "acting",
        "progress": 25,
        "steps": [
            {"id": "understand", "label": "Entender o objetivo", "status": "completed"},
            {"id": "act", "label": "Executar as ações necessárias", "status": "active"},
            {"id": "verify", "label": "Verificar o resultado", "status": "pending"},
            {"id": "report", "label": "Entregar o resultado", "status": "pending"},
        ],
        "evidence": [],
        "next_action": "Executar as ações necessárias",
        "completion_gate": "Só concluir quando houver resultado final ou evidência suficiente de execução.",
    }


def reconcile_tasks_with_runs() -> int:
    """Make persisted task state follow the canonical Run lifecycle."""
    from meuharness.agents import list_runs

    runs = {str(run.get("id") or ""): run for run in list_runs() if run.get("id")}
    items = _read()
    changed = 0
    now = _now()
    terminal = {
        "completed": ("completed", "completed", "Tarefa concluída"),
        "failed": ("failed", "failed", None),
        "cancelled": ("cancelled", "blocked", None),
        "interrupted": ("planned", "planned", "Execução interrompida; retomada disponível"),
    }
    for item in items:
        run = runs.get(str(item.get("run_id") or ""))
        if not run or run.get("status") not in terminal:
            continue
        status, activity, default_detail = terminal[str(run.get("status"))]
        detail = default_detail or str(run.get("error") or item.get("detail") or "")
        finished_at = run.get("finished_at") or item.get("finished_at") or now
        desired = {"status": status, "activity": activity, "tool": None, "finished_at": finished_at}
        if detail:
            desired["detail"] = detail[:240]
        if any(item.get(key) != value for key, value in desired.items()):
            item.update(desired)
            item["updated_at"] = now
            changed += 1
    if changed:
        _write(items)
    return changed


def list_tasks(
    *, agent_id: str | None = None, active_only: bool = False, limit: int = 200
) -> list[dict[str, Any]]:
    reconcile_tasks_with_runs()
    items = sorted(_read(), key=lambda x: x.get("updated_at", ""), reverse=True)
    if agent_id:
        items = [x for x in items if x.get("agent_id") == agent_id]
    if active_only:
        items = [x for x in items if x.get("status") in {"active", "waiting", "blocked", "planned"}]
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
        "conversation_id": run.get("conversation_id"),
        "title": title or "Tarefa", "prompt": prompt, "status": "active",
        "activity": "thinking", "tool": None, "detail": "Processando solicitação",
        "work": _work_template(prompt),
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

def update_work_by_run(
    run_id: str,
    *,
    phase: str | None = None,
    detail: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any] | None:
    """Update the visible Work-style objective/checklist for a Run task."""
    items = _read()
    for item in items:
        if item.get("run_id") != run_id:
            continue
        work = dict(item.get("work") or _work_template(str(item.get("prompt") or "")))
        steps = [dict(step) for step in work.get("steps") or []]
        by_id = {str(step.get("id")): step for step in steps}

        def mark(step_id: str, status: str, _by_id: dict[str, dict[str, Any]] = by_id) -> None:
            if step_id in _by_id:
                _by_id[step_id]["status"] = status

        if phase == "planned":
            mark("understand", "completed"); mark("act", "pending")
            mark("verify", "pending"); mark("report", "pending")
            work.update({"state": "planned", "progress": 25, "next_action": detail or "Executar o plano"})
        elif phase in {"acting", "thinking", "tool", "delegating", "waiting_agent"}:
            mark("understand", "completed"); mark("act", "active")
            work.update({"state": "acting", "progress": 45, "next_action": detail or "Executar as ações necessárias"})
        elif phase == "verifying":
            mark("understand", "completed"); mark("act", "completed"); mark("verify", "active")
            work.update({"state": "verifying", "progress": 75, "next_action": detail or "Verificar o resultado"})
        elif phase == "completed":
            for step_id in ("understand", "act", "verify", "report"):
                mark(step_id, "completed")
            work.update({"state": "completed", "progress": 100, "next_action": "Concluído"})
        elif phase == "waiting_approval":
            mark("understand", "completed"); mark("act", "blocked")
            work.update({"state": "waiting_approval", "progress": 45, "next_action": detail or "Aguardar aprovação"})
        elif phase in {"blocked", "failed", "cancelled"}:
            mark("act", "blocked")
            work.update({"state": phase, "next_action": detail or "Resolver bloqueio"})
        work["steps"] = steps
        if evidence:
            evidence_items = [str(x) for x in work.get("evidence") or [] if str(x).strip()]
            if evidence not in evidence_items:
                evidence_items.append(evidence)
            work["evidence"] = evidence_items[-8:]
        if detail:
            work["detail"] = detail[:240]
        item["work"] = work
        item["updated_at"] = _now()
        _write(items)
        return dict(item)
    return None
