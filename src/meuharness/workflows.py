"""Persistent workflows and execution engine for ACTIS GEN."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.storage import connect, init_db
from meuharness.tenant import (
    assert_organization_access,
    current_organization_id,
    normalize_organization_id,
    organization_context,
    resolve_inherited_organization_id,
)

NODE_TYPES = {"start", "agent", "condition", "end"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def list_workflows() -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM workflows ORDER BY updated_at DESC").fetchall()
    workflows = [json.loads(row["payload"]) for row in rows]
    return filter_by_organization(workflows)


def get_workflow(workflow_id: str) -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT payload FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    workflow = json.loads(row["payload"]) if row else None
    organization_id = current_organization_id()
    if workflow and organization_id:
        assert_organization_access(workflow, organization_id, resource="Workflow")
    return workflow


def _workflow_organization(data: dict, existing: dict | None = None) -> str | None:
    return resolve_inherited_organization_id(data.get("organization_id"), existing)


def _assert_workflow_agents(workflow: dict) -> None:
    from meuharness.agents import get_agent

    for node in workflow.get("nodes") or []:
        agent_id = str(node.get("agent_id") or "").strip()
        if agent_id:
            get_agent(agent_id)


def _normalize_node(node: dict) -> dict:
    node_type = str(node.get("type") or "agent").lower()
    if node_type not in NODE_TYPES:
        node_type = "agent"
    return {
        "id": str(node.get("id") or uuid4().hex),
        "type": node_type,
        "label": str(node.get("label") or "").strip()[:120],
        "agent_id": str(node.get("agent_id") or "").strip() or None,
        "prompt": str(node.get("prompt") or "").strip(),
        "condition": str(node.get("condition") or "").strip(),
        "x": max(0, float(node.get("x") or 40)),
        "y": max(0, float(node.get("y") or 40)),
    }


def _normalize_edge(edge: dict) -> dict:
    branch = str(edge.get("branch") or "next").lower()
    if branch not in {"next", "true", "false"}:
        branch = "next"
    return {
        "id": str(edge.get("id") or uuid4().hex),
        "from": str(edge.get("from") or ""),
        "to": str(edge.get("to") or ""),
        "branch": branch,
    }


def validate_workflow(data: dict) -> list[str]:
    errors: list[str] = []
    nodes = [_normalize_node(n) for n in list(data.get("nodes") or [])]
    edges = [_normalize_edge(e) for e in list(data.get("edges") or [])]

    node_ids = [n["id"] for n in nodes]
    ids = set(node_ids)
    if len(node_ids) != len(ids):
        errors.append("Existem nós com ID duplicado.")

    starts = [n for n in nodes if n["type"] == "start"]
    ends = [n for n in nodes if n["type"] == "end"]
    if len(starts) != 1:
        errors.append("O workflow precisa ter exatamente 1 nó Start.")
    if not ends:
        errors.append("O workflow precisa ter pelo menos 1 nó End.")

    outgoing: dict[str, list[dict]] = {node_id: [] for node_id in ids}
    incoming: dict[str, list[dict]] = {node_id: [] for node_id in ids}
    seen_connections: set[tuple[str, str, str]] = set()
    for edge in edges:
        key = (edge["from"], edge["to"], edge["branch"])
        if key in seen_connections:
            errors.append("Existe uma conexão duplicada.")
            continue
        seen_connections.add(key)
        if edge["from"] not in ids or edge["to"] not in ids:
            errors.append("Existe uma conexão apontando para nó inexistente.")
            continue
        if edge["from"] == edge["to"]:
            errors.append("Um nó não pode conectar diretamente a si mesmo.")
        outgoing[edge["from"]].append(edge)
        incoming[edge["to"]].append(edge)

    for node in nodes:
        node_id, node_type = node["id"], node["type"]
        label = node["label"] or node_id
        if node_type == "agent" and not node["agent_id"]:
            errors.append(f"Nó {label}: selecione um agente.")
        if node_type == "condition" and not node["condition"]:
            errors.append(f"Nó {label}: informe a condição.")
        if node_type == "start":
            if incoming[node_id]:
                errors.append(f"Nó {label}: Start não pode receber conexões.")
            if len(outgoing[node_id]) != 1 or outgoing[node_id][0]["branch"] != "next":
                errors.append(f"Nó {label}: Start precisa de exatamente uma saída NEXT.")
        elif node_type == "end":
            if outgoing[node_id]:
                errors.append(f"Nó {label}: End não pode ter saídas.")
        elif node_type == "condition":
            branches = [e["branch"] for e in outgoing[node_id]]
            if branches.count("true") != 1 or branches.count("false") != 1 or len(branches) != 2:
                errors.append(f"Nó {label}: condição precisa de uma saída TRUE e uma FALSE.")
        else:
            if len(outgoing[node_id]) != 1 or outgoing[node_id][0]["branch"] != "next":
                errors.append(f"Nó {label}: agente precisa de exatamente uma saída NEXT.")

    if len(starts) == 1:
        reachable: set[str] = set()
        stack = [starts[0]["id"]]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(e["to"] for e in outgoing.get(current, []) if e["to"] in ids)
        for node in nodes:
            if node["id"] not in reachable:
                errors.append(f"Nó {node['label'] or node['id']}: não é alcançável a partir do Start.")

        reverse_reachable: set[str] = {n["id"] for n in ends}
        stack = list(reverse_reachable)
        while stack:
            current = stack.pop()
            for edge in incoming.get(current, []):
                if edge["from"] not in reverse_reachable:
                    reverse_reachable.add(edge["from"])
                    stack.append(edge["from"])
        for node_id in reachable:
            if node_id not in reverse_reachable:
                node = next(n for n in nodes if n["id"] == node_id)
                errors.append(f"Nó {node['label'] or node_id}: não possui caminho até um End.")

    return list(dict.fromkeys(errors))


def save_workflow(data: dict, workflow_id: str | None = None) -> dict:
    init_db()
    now = _now()
    existing = get_workflow(workflow_id) if workflow_id else None
    if workflow_id and existing is None:
        raise ValueError("Workflow não encontrado.")

    raw_nodes = data["nodes"] if "nodes" in data else (existing or {}).get("nodes", [])
    raw_edges = data["edges"] if "edges" in data else (existing or {}).get("edges", [])
    nodes = [_normalize_node(n) for n in list(raw_nodes or [])]
    edges = [_normalize_edge(e) for e in list(raw_edges or [])]
    organization_id = _workflow_organization(data, existing)
    workflow = {
        "id": workflow_id or uuid4().hex,
        "name": str(data.get("name") if "name" in data else (existing or {}).get("name") or "Workflow").strip()[:100] or "Workflow",
        "description": str(data.get("description") if "description" in data else (existing or {}).get("description") or "").strip()[:500],
        "nodes": nodes,
        "edges": edges,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    if organization_id:
        workflow["organization_id"] = organization_id
    if current_organization_id():
        assert_organization_access(workflow, resource="Workflow")
    _assert_workflow_agents(workflow)
    payload = _json(workflow)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflows(id,name,payload,created_at,updated_at) VALUES(?,?,?,?,?)",
            (workflow["id"], workflow["name"], payload, workflow["created_at"], now),
        )
    return workflow


def delete_workflow(workflow_id: str) -> bool:
    workflow = get_workflow(workflow_id)
    if not workflow:
        return False
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
    return bool(cur.rowcount)


def list_workflow_runs(workflow_id: str | None = None, limit: int = 50) -> list[dict]:
    init_db()
    query = "SELECT * FROM workflow_runs"
    params: list[Any] = []
    if workflow_id:
        query += " WHERE workflow_id=?"
        params.append(workflow_id)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    runs = [dict(row) | {"trace": json.loads(row["trace"] or "[]")} for row in rows]
    if workflow_id:
        workflow = get_workflow(workflow_id)
        if not workflow:
            return []
        return [run for run in runs if run.get("workflow_id") == workflow_id]

    with connect() as conn:
        workflow_rows = conn.execute("SELECT id, payload FROM workflows").fetchall()
    workflow_organizations = {
        row["id"]: normalize_organization_id(json.loads(row["payload"]).get("organization_id"))
        for row in workflow_rows
    }
    organization_id = current_organization_id()
    if organization_id:
        runs = [
            run
            for run in runs
            if workflow_organizations.get(str(run.get("workflow_id") or "")) == organization_id
        ]
    return runs


def _condition_result(expression: str, text: str) -> bool:
    expr = str(expression or "").strip()
    source = str(text or "").strip()
    low_expr, low_source = expr.lower(), source.lower()
    if low_expr.startswith("contains:"):
        return low_expr.split(":", 1)[1].strip() in low_source
    if low_expr.startswith("equals:"):
        return low_source == low_expr.split(":", 1)[1].strip()
    if low_expr.startswith("not contains:"):
        return low_expr.split(":", 1)[1].strip() not in low_source
    return low_expr in low_source if low_expr else False


def _next_node(workflow: dict, node_id: str, branch: str = "next") -> str | None:
    edges = list(workflow.get("edges") or [])
    preferred = [e for e in edges if e.get("from") == node_id and e.get("branch") == branch]
    fallback = [e for e in edges if e.get("from") == node_id and e.get("branch") == "next"]
    edge = (preferred or fallback or [None])[0]
    return str(edge.get("to")) if edge else None


def _render_prompt(template: str, *, workflow_input: str, previous: str, label: str) -> str:
    text = str(template or "").strip() or "Execute sua etapa deste workflow usando o contexto recebido."
    return (
        text.replace("{{input}}", workflow_input)
        .replace("{{previous}}", previous)
        .replace("{{node.label}}", label)
    )


async def run_workflow(workflow_id: str, workflow_input: str = "", env_file: str | None = None) -> dict:
    workflow = get_workflow(workflow_id)
    if not workflow:
        raise ValueError("Workflow não encontrado.")
    organization_id = normalize_organization_id(workflow.get("organization_id"))
    if organization_id:
        with organization_context(organization_id):
            return await _run_workflow_impl(workflow_id, workflow_input, env_file)
    return await _run_workflow_impl(workflow_id, workflow_input, env_file)


async def _run_workflow_impl(workflow_id: str, workflow_input: str = "", env_file: str | None = None) -> dict:
    from meuharness.execution_service import execute_agent

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
            started = _now()
            item = {"node_id": node["id"], "type": node_type, "label": node.get("label") or "", "started_at": started}
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
                result = await execute_agent(str(node.get("agent_id") or ""), prompt, source="delegation", env_file=env_file)
                previous = result.text
                item.update({"status": "completed", "agent_id": node.get("agent_id"), "prompt": prompt, "output": result.text, "run_id": result.run_id, "finished_at": _now()})
                trace.append(item)
                current_id = _next_node(workflow, node["id"])
                continue
            current_id = _next_node(workflow, node["id"])
        status = "completed"
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        status, error = "failed", str(exc)
        trace.append({"node_id": current_id, "status": "failed", "error": error, "finished_at": _now()})
    finished_at = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE workflow_runs SET status=?,output=?,error=?,trace=?,finished_at=? WHERE id=?",
            (status, output, error, _json(trace), finished_at, run_id),
        )
    result = {"id": run_id, "workflow_id": workflow_id, "status": status, "input": workflow_input, "output": output, "error": error, "trace": trace, "started_at": started_at, "finished_at": finished_at}
    if error:
        raise RuntimeError(error)
    return result
