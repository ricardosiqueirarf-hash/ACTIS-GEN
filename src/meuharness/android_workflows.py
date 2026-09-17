"""Android-compatible workflow execution using the pure-Python agent runtime."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.android_execution import execute_agent_android
from meuharness.storage import connect
from meuharness.workflows import (
    _condition_result,
    _next_node,
    _render_prompt,
    get_workflow,
    validate_workflow,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def run_workflow_android(workflow_id: str, workflow_input: str = "", env_file: str | None = None) -> dict:
    del env_file
    workflow = get_workflow(workflow_id)
    if not workflow:
        raise ValueError("Workflow não encontrado.")
    errors = validate_workflow(workflow)
    if errors:
        raise ValueError("Workflow inválido: " + " ".join(errors))
    nodes = {str(n["id"]): n for n in workflow.get("nodes") or []}
    start = next(n for n in nodes.values() if n.get("type") == "start")
    run_id, started_at = uuid4().hex, _now()
    trace: list[dict[str, Any]] = []
    previous = str(workflow_input or "")
    current_id = _next_node(workflow, start["id"])
    status, output, error = "running", previous, None
    with connect() as conn:
        conn.execute(
            "INSERT INTO workflow_runs(id,workflow_id,status,input,output,error,trace,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, workflow_id, status, workflow_input, output, None, "[]", started_at, None),
        )
    try:
        steps = 0
        while current_id:
            steps += 1
            if steps > 50:
                raise RuntimeError("Workflow excedeu 50 etapas; possível ciclo sem saída.")
            node = nodes.get(current_id)
            if not node:
                raise RuntimeError("Workflow apontou para um nó inexistente.")
            node_type = node.get("type")
            item = {"node_id": node["id"], "type": node_type, "label": node.get("label") or "", "started_at": _now()}
            if node_type == "end":
                item.update({"status": "completed", "output": previous, "finished_at": _now()})
                trace.append(item)
                output = previous
                break
            if node_type == "condition":
                decision = _condition_result(str(node.get("condition") or ""), previous)
                item.update({"status": "completed", "decision": decision, "input": previous, "finished_at": _now()})
                trace.append(item)
                current_id = _next_node(workflow, node["id"], "true" if decision else "false")
                continue
            if node_type == "agent":
                prompt = _render_prompt(str(node.get("prompt") or ""), workflow_input=workflow_input, previous=previous, label=str(node.get("label") or ""))
                result = await execute_agent_android(str(node.get("agent_id") or ""), prompt, source="delegation")
                previous = result.text
                item.update({"status": "completed", "agent_id": node.get("agent_id"), "prompt": prompt, "output": result.text, "run_id": result.run_id, "finished_at": _now()})
                trace.append(item)
                current_id = _next_node(workflow, node["id"])
                continue
            raise RuntimeError(f"Tipo de nó não executável: {node_type}")
        status = "completed"
    except Exception as exc:
        status, error = "failed", str(exc)
        if current_id and current_id in nodes:
            trace.append({"node_id": current_id, "type": nodes[current_id].get("type"), "status": "failed", "error": error, "finished_at": _now()})
    finished_at = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE workflow_runs SET status=?,output=?,error=?,trace=?,finished_at=? WHERE id=?",
            (status, output, error, json.dumps(trace, ensure_ascii=False), finished_at, run_id),
        )
    result = {"id": run_id, "workflow_id": workflow_id, "status": status, "input": workflow_input, "output": output, "error": error, "trace": trace, "started_at": started_at, "finished_at": finished_at}
    if error:
        raise RuntimeError(error)
    return result
