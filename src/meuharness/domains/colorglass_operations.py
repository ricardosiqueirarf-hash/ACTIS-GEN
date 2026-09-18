"""Canonical ColorGlass operational state shared by specialized ACTIS agents."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.agents import get_agent
from meuharness.event_bus import emit_event
from meuharness.storage import load_collection, save_collection

COLLECTION = "colorglass_operations"
DEPARTMENTS = {"commercial", "quote", "procurement", "logistics", "finance"}
FINAL_PHASES = {"completed", "cancelled"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _company_for_agent(agent_id: str) -> str:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado")
    company_id = str(agent.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("O agente precisa pertencer a uma empresa")
    return company_id


def _read() -> list[dict[str, Any]]:
    return load_collection(COLLECTION)


def _write(items: list[dict[str, Any]]) -> None:
    save_collection(COLLECTION, items)


def _default_operation(*, company_id: str, customer: str, contact: str, source: str) -> dict[str, Any]:
    now = _now()
    return {
        "id": uuid4().hex,
        "company_id": company_id,
        "customer": str(customer or contact or "").strip(),
        "contact": str(contact or "").strip(),
        "phase": "commercial",
        "commercial": {"status": "collecting", "missing": []},
        "quote": {"status": "not_started", "mode": "new", "order_ref": "", "task_id": ""},
        "procurement": {"status": "not_checked", "missing_materials": [], "task_ids": []},
        "logistics": {"status": "not_scheduled", "address": "", "address_confirmed": False,
                      "scheduled_date": "", "courier": ""},
        "finance": {"status": "not_started", "total": 0.0, "paid": 0.0, "receivable": 0.0,
                    "due_date": ""},
        "items": [],
        "source": str(source or "user"),
        "created_at": now,
        "updated_at": now,
    }


def _matches(item: dict[str, Any], reference: str) -> bool:
    ref = str(reference or "").strip().lower()
    if not ref:
        return False
    quote = dict(item.get("quote") or {})
    values = {
        str(item.get("id") or "").lower(),
        str(item.get("contact") or "").lower(),
        str(item.get("customer") or "").lower(),
        str(quote.get("order_ref") or "").lower(),
    }
    return ref in values


def _active_for_contact(items: list[dict[str, Any]], company_id: str, contact: str) -> dict[str, Any] | None:
    rows = [
        item for item in items
        if str(item.get("company_id") or "") == company_id
        and str(item.get("contact") or "") == str(contact or "")
        and str(item.get("phase") or "") not in FINAL_PHASES
    ]
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows[0] if rows else None


def list_operations(agent_id: str, *, phase: str = "", limit: int = 100) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)
    rows = [dict(item) for item in _read() if str(item.get("company_id") or "") == company_id]
    if phase:
        rows = [item for item in rows if str(item.get("phase") or "") == str(phase)]
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows[:max(1, min(int(limit), 500))]


def get_operation(agent_id: str, reference: str) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    rows = [
        dict(item) for item in _read()
        if str(item.get("company_id") or "") == company_id and _matches(item, reference)
    ]
    if not rows:
        raise ValueError("Operação ColorGlass não encontrada")
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows[0]


def ensure_operation(agent_id: str, *, customer: str, contact: str = "", source: str = "user",
                     quote_mode: str = "new", order_ref: str = "") -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    items = _read()
    existing = None
    if order_ref:
        existing = next((
            item for item in items
            if str(item.get("company_id") or "") == company_id
            and str((item.get("quote") or {}).get("order_ref") or "") == str(order_ref)
        ), None)
    if existing is None and contact:
        existing = _active_for_contact(items, company_id, contact)
    if existing is None:
        existing = _default_operation(company_id=company_id, customer=customer, contact=contact, source=source)
        items.append(existing)
        emit_event("colorglass.operation.created", company_id=company_id,
                   operation_id=existing["id"], agent_id=agent_id)
    existing["customer"] = str(customer or existing.get("customer") or contact).strip()
    existing["contact"] = str(contact or existing.get("contact") or "").strip()
    quote = dict(existing.get("quote") or {})
    quote["mode"] = str(quote_mode or quote.get("mode") or "new")
    if order_ref:
        quote["order_ref"] = str(order_ref)
    existing["quote"] = quote
    existing["updated_at"] = _now()
    _write(items)
    return dict(existing)


def _phase_from_state(operation: dict[str, Any]) -> str:
    finance = dict(operation.get("finance") or {})
    logistics = dict(operation.get("logistics") or {})
    procurement = dict(operation.get("procurement") or {})
    quote = dict(operation.get("quote") or {})
    if str(logistics.get("status") or "") == "delivered" and str(finance.get("status") or "") == "paid":
        return "completed"
    if str(logistics.get("status") or "") in {"scheduled", "out_for_delivery", "delivered"}:
        return "logistics"
    if str(procurement.get("status") or "") in {"missing", "contacting_suppliers", "quoted", "purchasing", "ready"}:
        return "procurement"
    if str(quote.get("status") or "") in {"quoted", "completed", "verified"}:
        return "quoted"
    return "commercial"


def patch_department(agent_id: str, reference: str, department: str, patch: dict[str, Any]) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    department = str(department or "").strip().lower()
    if department not in DEPARTMENTS:
        raise ValueError("Departamento operacional inválido")
    items = _read()
    for item in items:
        if str(item.get("company_id") or "") != company_id or not _matches(item, reference):
            continue
        section = dict(item.get(department) or {})
        section.update(dict(patch or {}))
        item[department] = section
        item["phase"] = _phase_from_state(item)
        item["updated_at"] = _now()
        _write(items)
        emit_event("colorglass.operation.updated", company_id=company_id,
                   operation_id=item["id"], agent_id=agent_id, department=department)
        return dict(item)
    raise ValueError("Operação ColorGlass não encontrada")


def sync_quote_state(agent_id: str, contact: str, quote: dict[str, Any], *,
                     customer: str = "", source: str = "whatsapp") -> dict[str, Any]:
    fields = dict(quote.get("fields") or {})
    missing = list(quote.get("missing_fields") or [])
    mode = str(quote.get("quote_mode") or "new")
    stage = str(quote.get("quote_phase") or "commercial")
    order_ref = str(quote.get("order_ref") or "")
    operation = ensure_operation(
        agent_id, customer=customer or contact, contact=contact, source=source,
        quote_mode=mode, order_ref=order_ref,
    )
    operation = patch_department(agent_id, operation["id"], "commercial", {
        "status": (
            ("technical_collection" if missing else "technical_ready") if stage == "technical"
            else ("needs_information" if missing else "ready_for_quote")
        ),
        "stage": stage,
        "missing": missing,
        "last_customer_text": str(quote.get("last_customer_text") or "")[:2000],
    })
    quote_patch = {
        "status": str(quote.get("status") or "collecting"),
        "mode": mode,
        "phase": stage,
        "order_ref": order_ref,
        "task_id": str(quote.get("company_task_id") or ""),
        "fields": fields,
        "missing": missing,
    }
    if quote.get("company_task_result"):
        quote_patch["result"] = str(quote.get("company_task_result") or "")[:6000]
    return patch_department(agent_id, operation["id"], "quote", quote_patch)


def _json_object(value: str) -> dict[str, Any]:
    if not str(value or "").strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("details_json precisa ser um objeto JSON")
    return parsed


def build_colorglass_operation_tools(agent_id: str) -> list[object]:
    agent = get_agent(agent_id) or {}
    capabilities = {str(value) for value in list(agent.get("company_capabilities") or [])}
    allowed_departments: set[str] = set()
    if "customer-intake" in capabilities:
        allowed_departments.add("commercial")
    if capabilities & {"quote", "orcamento"}:
        allowed_departments.add("quote")
    if capabilities & {"procurement", "purchasing"}:
        allowed_departments.add("procurement")
    if capabilities & {"logistics", "order-tracking"}:
        allowed_departments.add("logistics")
    if capabilities & {"finance", "financial-reporting"}:
        allowed_departments.add("finance")

    def colorglass_ops_list(phase: str = "", limit: int = 50) -> str:
        """Liste a fonte de verdade operacional ColorGlass da empresa."""
        return json.dumps({"operations": list_operations(agent_id, phase=phase, limit=limit)},
                          ensure_ascii=False, default=str)

    def colorglass_ops_get(reference: str) -> str:
        """Leia uma operação por ID, contato, cliente exato ou número do pedido/orçamento."""
        return json.dumps(get_operation(agent_id, reference), ensure_ascii=False, default=str)

    def colorglass_ops_upsert(customer: str, contact: str = "", quote_mode: str = "new",
                              order_ref: str = "") -> str:
        """Crie ou recupere a operação canônica do cliente sem duplicar uma operação ativa."""
        return json.dumps(ensure_operation(
            agent_id, customer=customer, contact=contact, source="agent",
            quote_mode=quote_mode, order_ref=order_ref,
        ), ensure_ascii=False, default=str)

    def colorglass_ops_update(reference: str, department: str, status: str = "",
                              details_json: str = "{}") -> str:
        """Atualize apenas seu departamento na operação. details_json deve ser objeto JSON."""
        normalized_department = str(department or "").strip().lower()
        if normalized_department not in allowed_departments:
            raise ValueError("Este agente não tem permissão para alterar esse departamento")
        patch = _json_object(details_json)
        if status:
            patch["status"] = status
        return json.dumps(patch_department(agent_id, reference, normalized_department, patch),
                          ensure_ascii=False, default=str)

    return [colorglass_ops_list, colorglass_ops_get, colorglass_ops_upsert, colorglass_ops_update]
