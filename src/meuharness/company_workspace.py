"""Native per-company and per-agent operational workspaces for ACTIS GEN."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from meuharness.event_bus import emit_event
from meuharness.storage import load_collection, mutate_collection

COLLECTION = "company_work_items"
VALID_STATUSES = {
    "inbox",
    "pending",
    "in_progress",
    "waiting",
    "blocked",
    "completed",
    "cancelled",
}
ACTIVE_STATUSES = {"inbox", "pending", "in_progress", "waiting", "blocked"}
VALID_PRIORITIES = {"low", "normal", "high", "critical"}
VALID_OWNER_TYPES = {"agent", "operator"}
OPERATOR_OWNER_ID = "operator"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: object, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _normalize_kind(value: object) -> str:
    raw = _clean_text(value, limit=100).lower()
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-") or "task"


def _normalize_refs(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("refs precisa ser um objeto.")
    result: dict[str, str] = {}
    for key, item in value.items():
        k = _normalize_kind(key)
        text = _clean_text(item, limit=300)
        if text:
            result[k] = text
    return result


def _normalize_notes(value: object) -> list[str]:
    if value is None:
        return []
    rows = value if isinstance(value, list) else [value]
    return [
        _clean_text(item, limit=1200)
        for item in rows
        if _clean_text(item, limit=1200)
    ]


def _normalize_metadata(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("metadata precisa ser um objeto.")
    return dict(value)


def _parse_due_at(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("due_at precisa ser ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _assert_company(company_id: str) -> dict[str, Any]:
    from meuharness.agents import get_company

    company = get_company(company_id)
    if not company:
        raise ValueError("Empresa não encontrada.")
    return company


def _assert_owner(company_id: str, owner_type: str, owner_id: str) -> tuple[str, str]:
    owner_type = str(owner_type or "agent").strip().lower()
    if owner_type not in VALID_OWNER_TYPES:
        raise ValueError("owner_type precisa ser agent ou operator.")
    if owner_type == "operator":
        return owner_type, OPERATOR_OWNER_ID

    from meuharness.agents import get_agent

    owner_id = str(owner_id or "").strip()
    agent = get_agent(owner_id)
    if not agent or str(agent.get("company_id") or "") != company_id:
        raise ValueError("Agente dono do WorkItem não pertence à empresa.")
    return owner_type, owner_id


def _assert_status(value: object) -> str:
    status = str(value or "pending").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"Status inválido: {status}")
    return status


def _assert_priority(value: object) -> str:
    priority = str(value or "normal").strip().lower()
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Prioridade inválida: {priority}")
    return priority


def _is_overdue(item: dict[str, Any], *, now: datetime | None = None) -> bool:
    if item.get("status") not in ACTIVE_STATUSES or not item.get("due_at"):
        return False
    try:
        due = datetime.fromisoformat(str(item["due_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return due.astimezone(UTC) < (now or datetime.now(UTC))


def _decorate(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row["overdue"] = _is_overdue(row)
    return row


def list_work_items(
    company_id: str,
    *,
    owner_type: str = "",
    owner_id: str = "",
    status: str = "",
    priority: str = "",
    active_only: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    company_id = str(company_id or "").strip()
    if not company_id:
        raise ValueError("company_id é obrigatório.")
    rows = [
        dict(x)
        for x in load_collection(COLLECTION)
        if str(x.get("company_id") or "") == company_id
    ]
    if owner_type:
        rows = [x for x in rows if x.get("owner_type") == owner_type]
    if owner_id:
        rows = [x for x in rows if x.get("owner_id") == owner_id]
    if status:
        rows = [x for x in rows if x.get("status") == status]
    if priority:
        rows = [x for x in rows if x.get("priority") == priority]
    if active_only:
        rows = [x for x in rows if x.get("status") in ACTIVE_STATUSES]
    rows.sort(
        key=lambda x: (
            x.get("status") in {"completed", "cancelled"},
            {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(
                str(x.get("priority")), 9
            ),
            str(x.get("due_at") or "9999"),
            str(x.get("updated_at") or ""),
        )
    )
    return [_decorate(x) for x in rows[: max(1, min(int(limit), 1000))]]


def get_work_item(item_id: str) -> dict[str, Any] | None:
    row = next(
        (dict(x) for x in load_collection(COLLECTION) if x.get("id") == item_id),
        None,
    )
    return _decorate(row) if row else None


def create_work_item(
    *,
    company_id: str,
    owner_type: str,
    owner_id: str,
    title: str,
    source: str = "agent",
    kind: str = "task",
    status: str = "pending",
    priority: str = "normal",
    refs: dict[str, Any] | None = None,
    next_action: str = "",
    blocked_by: str = "",
    due_at: str | None = None,
    notes: list[str] | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    company_id = str(company_id or "").strip()
    _assert_company(company_id)
    owner_type, owner_id = _assert_owner(company_id, owner_type, owner_id)
    title = _clean_text(title, limit=240)
    if not title:
        raise ValueError("Título do WorkItem é obrigatório.")
    now = _now()
    status = _assert_status(status)
    item = {
        "id": uuid4().hex,
        "company_id": company_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "source": _clean_text(source, limit=120) or "agent",
        "kind": _normalize_kind(kind),
        "title": title,
        "status": status,
        "priority": _assert_priority(priority),
        "refs": _normalize_refs(refs),
        "next_action": _clean_text(next_action, limit=600),
        "blocked_by": _clean_text(blocked_by, limit=600),
        "due_at": _parse_due_at(due_at),
        "notes": _normalize_notes(notes),
        "metadata": _normalize_metadata(metadata),
        "created_at": now,
        "updated_at": now,
        "finished_at": now if status in {"completed", "cancelled"} else None,
    }

    def append(items: list[dict[str, Any]]) -> dict[str, Any]:
        items.append(item)
        return dict(item)

    created = mutate_collection(COLLECTION, append)
    emit_event(
        "company.workspace.item.created",
        company_id=company_id,
        work_item_id=created["id"],
        owner_type=owner_type,
        owner_id=owner_id,
        kind=created["kind"],
        status=status,
    )
    return _decorate(created)


def update_work_item(
    item_id: str,
    *,
    expected_company_id: str = "",
    expected_owner_type: str = "",
    expected_owner_id: str = "",
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changes = dict(changes or {})
    immutable = {"id", "company_id", "created_at"}
    if immutable & set(changes):
        raise ValueError("id, company_id e created_at são imutáveis.")

    def mutate(items: list[dict[str, Any]]) -> dict[str, Any]:
        for item in items:
            if item.get("id") != item_id:
                continue
            company_id = str(item.get("company_id") or "")
            if expected_company_id and company_id != expected_company_id:
                raise PermissionError("WorkItem pertence a outra empresa.")
            if expected_owner_type and item.get("owner_type") != expected_owner_type:
                raise PermissionError("WorkItem pertence a outro workspace.")
            if expected_owner_id and item.get("owner_id") != expected_owner_id:
                raise PermissionError("WorkItem pertence a outro workspace.")

            if "owner_type" in changes or "owner_id" in changes:
                new_type = str(
                    changes.get("owner_type", item.get("owner_type")) or ""
                )
                new_id = str(changes.get("owner_id", item.get("owner_id")) or "")
                new_type, new_id = _assert_owner(company_id, new_type, new_id)
                item["owner_type"] = new_type
                item["owner_id"] = new_id
            if "title" in changes:
                title = _clean_text(changes["title"], limit=240)
                if not title:
                    raise ValueError("Título do WorkItem é obrigatório.")
                item["title"] = title
            if "source" in changes:
                item["source"] = _clean_text(changes["source"], limit=120) or "agent"
            if "kind" in changes:
                item["kind"] = _normalize_kind(changes["kind"])
            if "status" in changes:
                item["status"] = _assert_status(changes["status"])
            if "priority" in changes:
                item["priority"] = _assert_priority(changes["priority"])
            if "refs" in changes:
                item["refs"] = _normalize_refs(changes["refs"])
            if "next_action" in changes:
                item["next_action"] = _clean_text(
                    changes["next_action"], limit=600
                )
            if "blocked_by" in changes:
                item["blocked_by"] = _clean_text(changes["blocked_by"], limit=600)
            if "due_at" in changes:
                item["due_at"] = _parse_due_at(changes["due_at"])
            if "notes" in changes:
                item["notes"] = _normalize_notes(changes["notes"])
            if "append_note" in changes:
                note = _clean_text(changes["append_note"], limit=1200)
                if note:
                    item["notes"] = [*list(item.get("notes") or []), note][-50:]
            if "metadata" in changes:
                item["metadata"] = _normalize_metadata(changes["metadata"])
            item["updated_at"] = _now()
            if item.get("status") in {"completed", "cancelled"}:
                item["finished_at"] = item.get("finished_at") or item["updated_at"]
            else:
                item["finished_at"] = None
            return dict(item)
        raise ValueError("WorkItem não encontrado.")

    updated = mutate_collection(COLLECTION, mutate)
    event_type = (
        "company.workspace.item.completed"
        if updated.get("status") == "completed"
        else "company.workspace.item.cancelled"
        if updated.get("status") == "cancelled"
        else "company.workspace.item.updated"
    )
    emit_event(
        event_type,
        company_id=updated["company_id"],
        work_item_id=updated["id"],
        owner_type=updated["owner_type"],
        owner_id=updated["owner_id"],
        status=updated["status"],
    )
    return _decorate(updated)


def company_workspace_snapshot(
    company_id: str, *, active_only: bool = False
) -> dict[str, Any]:
    from meuharness.agents import get_company, list_agents
    from meuharness.company_bus import reconcile_company_task_work_items

    company = get_company(company_id)
    if not company:
        raise ValueError("Empresa não encontrada.")
    reconcile_company_task_work_items(company_id)
    rows = list_work_items(company_id, active_only=active_only, limit=1000)
    agents = [
        a
        for a in list_agents()
        if str(a.get("company_id") or "") == company_id
    ]
    status_counts = {status: 0 for status in VALID_STATUSES}
    priority_counts = {priority: 0 for priority in VALID_PRIORITIES}
    for item in rows:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        priority_counts[item["priority"]] = (
            priority_counts.get(item["priority"], 0) + 1
        )

    by_agent = []
    for agent in agents:
        agent_rows = [
            x
            for x in rows
            if x.get("owner_type") == "agent"
            and x.get("owner_id") == agent.get("id")
        ]
        by_agent.append(
            {
                "agent_id": agent.get("id"),
                "name": agent.get("name"),
                "status": agent.get("status"),
                "counts": {
                    status: sum(
                        1 for x in agent_rows if x.get("status") == status
                    )
                    for status in VALID_STATUSES
                },
                "overdue": sum(1 for x in agent_rows if x.get("overdue")),
                "items": agent_rows,
            }
        )
    operator_items = [
        x for x in rows if x.get("owner_type") == "operator"
    ]
    return {
        "company": company,
        "summary": {
            "total": len(rows),
            "active": sum(
                1 for x in rows if x.get("status") in ACTIVE_STATUSES
            ),
            "overdue": sum(1 for x in rows if x.get("overdue")),
            "status": status_counts,
            "priority": priority_counts,
        },
        "operator": {
            "owner_id": OPERATOR_OWNER_ID,
            "count": len(operator_items),
            "overdue": sum(1 for x in operator_items if x.get("overdue")),
            "items": operator_items,
        },
        "agents": by_agent,
    }


def agent_workspace_snapshot(
    agent_id: str, *, active_only: bool = False
) -> dict[str, Any]:
    from meuharness.agents import get_agent, get_company
    from meuharness.company_bus import reconcile_company_task_work_items

    agent = get_agent(agent_id)
    if not agent or not agent.get("company_id"):
        return {}
    company_id = str(agent["company_id"])
    reconcile_company_task_work_items(company_id)
    items = list_work_items(
        company_id,
        owner_type="agent",
        owner_id=agent_id,
        active_only=active_only,
        limit=500,
    )
    return {
        "company": get_company(company_id),
        "agent": {"id": agent.get("id"), "name": agent.get("name")},
        "summary": {
            "total": len(items),
            "active": sum(
                1 for x in items if x.get("status") in ACTIVE_STATUSES
            ),
            "overdue": sum(1 for x in items if x.get("overdue")),
            "by_status": {
                status: sum(
                    1 for x in items if x.get("status") == status
                )
                for status in VALID_STATUSES
            },
        },
        "items": items,
    }


def company_workspace_instructions(agent_id: str) -> str:
    snapshot = agent_workspace_snapshot(agent_id, active_only=True)
    if not snapshot:
        return ""
    compact = {
        "summary": snapshot["summary"],
        "items": [
            {
                "id": x["id"],
                "title": x["title"],
                "status": x["status"],
                "priority": x["priority"],
                "next_action": x.get("next_action"),
                "blocked_by": x.get("blocked_by"),
                "due_at": x.get("due_at"),
                "refs": x.get("refs"),
            }
            for x in snapshot["items"][:12]
        ],
    }
    return (
        "\n\nCOMPANY WORKSPACE ACTIS: este é o seu organizador operacional pessoal "
        "dentro da empresa. Use WorkItems para registrar trabalho, próxima ação, "
        "bloqueios e prazo. O Workspace NÃO é ERP nem fonte de verdade de "
        "cliente/pedido/financeiro: fatos canônicos devem continuar no sistema "
        "estruturado do domínio. Memória guarda conhecimento; Workspace guarda "
        "trabalho; ERP/core guarda fatos. Ao concluir ou bloquear uma atividade, "
        "atualize o WorkItem correspondente. Estado atual:\n"
        + json.dumps(compact, ensure_ascii=False)
    )


def build_company_workspace_tools(agent_id: str) -> list[object]:
    from meuharness.agents import get_agent

    agent = get_agent(agent_id)
    if not agent or not agent.get("company_id"):
        return []
    company_id = str(agent["company_id"])

    def company_workspace_get() -> str:
        """Veja seu workspace operacional pessoal nesta empresa."""
        return json.dumps(agent_workspace_snapshot(agent_id), ensure_ascii=False)

    def company_workspace_list(
        status: Annotated[str, "Status; vazio=todos"] = "",
        active_only: Annotated[bool, "Somente itens ativos"] = False,
        limit: Annotated[int, "Máximo de itens"] = 100,
    ) -> str:
        """Liste somente os WorkItems deste agente na própria empresa."""
        rows = list_work_items(
            company_id,
            owner_type="agent",
            owner_id=agent_id,
            status=status,
            active_only=active_only,
            limit=limit,
        )
        return json.dumps(rows, ensure_ascii=False)

    def company_workspace_create(
        title: Annotated[str, "Título curto da atividade"],
        kind: Annotated[str, "Tipo da atividade"] = "task",
        priority: Annotated[str, "low, normal, high ou critical"] = "normal",
        next_action: Annotated[str, "Próxima ação concreta"] = "",
        due_at: Annotated[str, "Prazo ISO 8601; vazio=sem prazo"] = "",
        refs_json: Annotated[
            str, "Objeto JSON com referências canônicas"
        ] = "{}",
    ) -> str:
        """Crie um WorkItem no seu próprio workspace."""
        try:
            refs = json.loads(refs_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("refs_json precisa ser JSON válido.") from exc
        item = create_work_item(
            company_id=company_id,
            owner_type="agent",
            owner_id=agent_id,
            title=title,
            kind=kind,
            priority=priority,
            next_action=next_action,
            due_at=due_at or None,
            refs=refs,
            source=f"agent:{agent_id}",
        )
        return json.dumps(item, ensure_ascii=False)

    def company_workspace_update(
        work_item_id: Annotated[str, "ID do WorkItem"],
        status: Annotated[str, "Novo status; vazio=preserva"] = "",
        priority: Annotated[str, "Nova prioridade; vazio=preserva"] = "",
        next_action: Annotated[str, "Nova próxima ação; vazio=preserva"] = "",
        blocked_by: Annotated[str, "Bloqueio; vazio=preserva"] = "",
        append_note: Annotated[str, "Nota a acrescentar; vazio=nenhuma"] = "",
    ) -> str:
        """Atualize somente um WorkItem que pertence ao seu próprio workspace."""
        changes: dict[str, Any] = {}
        if status:
            changes["status"] = status
        if priority:
            changes["priority"] = priority
        if next_action:
            changes["next_action"] = next_action
        if blocked_by:
            changes["blocked_by"] = blocked_by
        if append_note:
            changes["append_note"] = append_note
        item = update_work_item(
            work_item_id,
            expected_company_id=company_id,
            expected_owner_type="agent",
            expected_owner_id=agent_id,
            changes=changes,
        )
        return json.dumps(item, ensure_ascii=False)

    return [
        company_workspace_get,
        company_workspace_list,
        company_workspace_create,
        company_workspace_update,
    ]
