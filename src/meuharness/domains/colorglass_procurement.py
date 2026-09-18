"""ColorGlass procurement domain with an explicit supplier allowlist."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from meuharness.agents import get_agent
from meuharness.storage import load_collection, save_collection

COLLECTION = "colorglass_suppliers"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _company_for_agent(agent_id: str) -> str:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado")
    company_id = str(agent.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("Agente sem empresa")
    return company_id


def normalize_contact(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))

def list_suppliers(agent_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)
    rows = [
        dict(item) for item in load_collection(COLLECTION)
        if str(item.get("company_id") or "") == company_id
    ]
    if active_only:
        rows = [item for item in rows if item.get("active", True)]
    rows.sort(key=lambda item: str(item.get("name") or "").lower())
    return rows


def add_supplier(agent_id: str, name: str, contact: str, *, categories: list[str] | None = None,
                 notes: str = "") -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    normalized = normalize_contact(contact)
    if not str(name or "").strip() or not normalized:
        raise ValueError("Fornecedor precisa de nome e contato")
    items = load_collection(COLLECTION)
    existing = next((
        item for item in items
        if str(item.get("company_id") or "") == company_id
        and normalize_contact(str(item.get("contact") or "")) == normalized
    ), None)
    now = _now()
    if existing is None:
        existing = {"id": uuid4().hex, "company_id": company_id, "created_at": now}
        items.append(existing)

    existing.update({
        "name": str(name).strip(),
        "contact": normalized,
        "categories": list(dict.fromkeys(str(x).strip().lower() for x in categories or [] if str(x).strip())),
        "notes": str(notes or "").strip(),
        "active": True,
        "updated_at": now,
    })
    save_collection(COLLECTION, items)
    return dict(existing)


def authorized_supplier(agent_id: str, contact: str) -> dict[str, Any]:
    normalized = normalize_contact(contact)
    supplier = next((
        item for item in list_suppliers(agent_id)
        if normalize_contact(str(item.get("contact") or "")) == normalized
    ), None)
    if not supplier:
        raise ValueError("Contato não está na lista autorizada de fornecedores da ColorGlass")
    return supplier


def assert_authorized_supplier_for_company(company_id: str, contact: str) -> dict[str, Any]:
    normalized = normalize_contact(contact)
    supplier = next((
        dict(item) for item in load_collection(COLLECTION)
        if str(item.get("company_id") or "") == str(company_id)
        and item.get("active", True)
        and normalize_contact(str(item.get("contact") or "")) == normalized
    ), None)

    if not supplier:
        raise ValueError("Contato não está na lista autorizada de fornecedores da empresa")
    return supplier


def build_procurement_tools(agent_id: str, *, run_id: str | None = None,
                            env_file: str | None = None) -> list[object]:
    def procurement_list_suppliers() -> str:
        """Liste somente fornecedores autorizados da empresa."""
        return json.dumps({"suppliers": list_suppliers(agent_id)}, ensure_ascii=False)

    def procurement_add_supplier(
        name: Annotated[str, "Nome do fornecedor"],
        contact: Annotated[str, "Telefone/contato WhatsApp autorizado"],
        categories_csv: Annotated[str, "Categorias separadas por vírgula"] = "",
        notes: Annotated[str, "Observações"] = "",
    ) -> str:
        """Autorize explicitamente um fornecedor para o fluxo de Compras."""
        categories = [x.strip() for x in categories_csv.split(",") if x.strip()]
        return json.dumps(add_supplier(agent_id, name, contact, categories=categories, notes=notes),
                          ensure_ascii=False)

    async def procurement_contact_supplier(
        operation_ref: Annotated[str, "ID/cliente/contato/pedido da operação ColorGlass"],
        supplier_contact: Annotated[str, "Contato já autorizado do fornecedor"],
        message: Annotated[str, "Mensagem objetiva a enviar ao fornecedor"],
    ) -> str:
        """Peça ao transporte WhatsApp da empresa para contatar um fornecedor autorizado."""
        supplier = authorized_supplier(agent_id, supplier_contact)
        from meuharness.company_bus import create_company_task, dispatch_company_task
        from meuharness.domains.colorglass_operations import get_operation, patch_department

        operation = get_operation(agent_id, operation_ref)
        task = create_company_task(
            company_id=_company_for_agent(agent_id),
            source_agent_id=agent_id,
            kind="supplier-contact",
            objective=(
                "Envie exatamente a mensagem do payload ao contato de fornecedor autorizado. "
                "Não altere destinatário, não converse com clientes e não acrescente dados internos."
            ),
            payload={
                "contact": supplier["contact"],
                "supplier": supplier["name"],
                "message": str(message or "").strip(),
                "operation_id": operation["id"],
            },
        )
        if task.get("assigned_agent_id"):
            task = await dispatch_company_task(task["id"], env_file=env_file, parent_run_id=run_id)
        procurement = dict(operation.get("procurement") or {})
        task_ids = list(dict.fromkeys([*list(procurement.get("task_ids") or []), task["id"]]))
        patch_department(agent_id, operation["id"], "procurement", {
            "status": "contacting_suppliers" if task.get("status") != "completed" else "quoted",
            "task_ids": task_ids,
            "last_supplier": supplier["name"],
        })
        return json.dumps(task, ensure_ascii=False, default=str)

    return [procurement_list_suppliers, procurement_add_supplier, procurement_contact_supplier]
