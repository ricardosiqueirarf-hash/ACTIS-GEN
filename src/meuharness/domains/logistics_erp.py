"""Company-scoped logistics ERP/Kanban operated by ACTIS agents."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from meuharness.agents import get_agent
from meuharness.storage import connect

STAGES = ("aguardando", "producao", "pronto", "entrega", "entregue")
PRIORITIES = ("low", "normal", "high", "urgent")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _company_for_agent(agent_id: str) -> str:
    agent = get_agent(agent_id)
    company_id = str((agent or {}).get("company_id") or "").strip()
    if not agent:
        raise ValueError("Agente não encontrado")
    if not company_id:
        raise ValueError("O agente precisa pertencer a uma empresa para operar o ERP logístico")
    return company_id


def init_logistics_schema() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS logistics_orders (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL,
            customer TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'aguardando',
            priority TEXT NOT NULL DEFAULT 'normal',
            promised_date TEXT,
            scheduled_date TEXT,
            notes TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'user',
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logistics_orders_company_stage
            ON logistics_orders(company_id, stage, updated_at DESC);
        CREATE TABLE IF NOT EXISTS logistics_pending (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            due_date TEXT,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES logistics_orders(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_logistics_pending_order
            ON logistics_pending(order_id, status, updated_at DESC);
        """)


def _row_order(row: Any, pending: list[dict[str, Any]]) -> dict[str, Any]:
    item = dict(row)
    item["pending"] = pending
    item["pending_open"] = sum(1 for p in pending if p.get("status") == "open")
    return item


def board_snapshot(agent_id: str) -> dict[str, Any]:
    init_logistics_schema()
    company_id = _company_for_agent(agent_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM logistics_orders WHERE company_id=? ORDER BY updated_at DESC", (company_id,)
        ).fetchall()
        ids = [r["id"] for r in rows]
        pending_by_order: dict[str, list[dict[str, Any]]] = {oid: [] for oid in ids}
        if ids:
            marks = ",".join("?" for _ in ids)
            for p in conn.execute(
                f"SELECT * FROM logistics_pending WHERE order_id IN ({marks}) ORDER BY status,updated_at DESC", ids
            ).fetchall():
                pending_by_order.setdefault(p["order_id"], []).append(dict(p))
    orders = [_row_order(r, pending_by_order.get(r["id"], [])) for r in rows]
    today = date.today().isoformat()
    active = [o for o in orders if o["stage"] != "entregue"]
    overdue = [o for o in active if o.get("promised_date") and o["promised_date"] < today]
    due_today = [o for o in active if o.get("promised_date") == today]
    return {
        "agent_id": agent_id,
        "company_id": company_id,
        "stages": list(STAGES),
        "orders": orders,
        "stats": {
            "active": len(active),
            "overdue": len(overdue),
            "due_today": len(due_today),
            "pending_open": sum(int(o["pending_open"]) for o in active),
        },
    }


def create_order(agent_id: str, data: dict[str, Any], *, source: str = "user") -> dict[str, Any]:
    init_logistics_schema()
    company_id = _company_for_agent(agent_id)
    title = str(data.get("title") or "").strip()
    customer = str(data.get("customer") or "").strip()
    if not title:
        raise ValueError("Título do pedido é obrigatório")
    if not customer:
        raise ValueError("Cliente é obrigatório")
    stage = str(data.get("stage") or "aguardando").strip().lower()
    priority = str(data.get("priority") or "normal").strip().lower()
    if stage not in STAGES:
        raise ValueError("Etapa logística inválida")
    if priority not in PRIORITIES:
        raise ValueError("Prioridade inválida")
    now, order_id = _now(), uuid.uuid4().hex
    with connect() as conn:
        conn.execute("""INSERT INTO logistics_orders
            (id,company_id,customer,reference,title,stage,priority,promised_date,scheduled_date,notes,source,created_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            order_id, company_id, customer, str(data.get("reference") or "").strip(), title, stage, priority,
            str(data.get("promised_date") or "").strip() or None,
            str(data.get("scheduled_date") or "").strip() or None,
            str(data.get("notes") or "").strip(), source, agent_id, now, now,
        ))
    return get_order(agent_id, order_id)


def get_order(agent_id: str, order_id: str) -> dict[str, Any]:
    init_logistics_schema()
    company_id = _company_for_agent(agent_id)
    with connect() as conn:
        row = conn.execute("SELECT * FROM logistics_orders WHERE id=? AND company_id=?", (order_id, company_id)).fetchone()
        if not row:
            raise ValueError("Pedido não encontrado neste ERP")
        pending = [dict(r) for r in conn.execute(
            "SELECT * FROM logistics_pending WHERE order_id=? ORDER BY status,updated_at DESC", (order_id,)
        ).fetchall()]
    return _row_order(row, pending)


def update_order(agent_id: str, order_id: str, data: dict[str, Any]) -> dict[str, Any]:
    current = get_order(agent_id, order_id)
    allowed = {"customer", "reference", "title", "stage", "priority", "promised_date", "scheduled_date", "notes"}
    values = {key: data[key] for key in allowed if key in data}
    if "stage" in values:
        values["stage"] = str(values["stage"] or "").strip().lower()
        if values["stage"] not in STAGES:
            raise ValueError("Etapa logística inválida")
    if "priority" in values:
        values["priority"] = str(values["priority"] or "").strip().lower()
        if values["priority"] not in PRIORITIES:
            raise ValueError("Prioridade inválida")
    if "title" in values and not str(values["title"] or "").strip():
        raise ValueError("Título do pedido é obrigatório")
    if "customer" in values and not str(values["customer"] or "").strip():
        raise ValueError("Cliente é obrigatório")
    if not values:
        return current
    fields, params = [], []
    for key, value in values.items():
        fields.append(f"{key}=?")
        if key in {"promised_date", "scheduled_date"}:
            value = str(value or "").strip() or None
        elif isinstance(value, str):
            value = value.strip()
        params.append(value)
    params.extend([_now(), order_id, current["company_id"]])
    with connect() as conn:
        conn.execute(f"UPDATE logistics_orders SET {','.join(fields)},updated_at=? WHERE id=? AND company_id=?", params)
    return get_order(agent_id, order_id)


def delete_order(agent_id: str, order_id: str) -> dict[str, Any]:
    current = get_order(agent_id, order_id)
    with connect() as conn:
        conn.execute("DELETE FROM logistics_orders WHERE id=? AND company_id=?", (order_id, current["company_id"]))
    return {"ok": True, "deleted": order_id}


def add_pending(agent_id: str, order_id: str, title: str, due_date: str = "", *, source: str = "user") -> dict[str, Any]:
    get_order(agent_id, order_id)
    text = str(title or "").strip()
    if not text:
        raise ValueError("Pendência é obrigatória")
    now, pending_id = _now(), uuid.uuid4().hex
    with connect() as conn:
        conn.execute("""INSERT INTO logistics_pending
            (id,order_id,title,status,due_date,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
            (pending_id, order_id, text, "open", str(due_date or "").strip() or None, source, now, now))
    return get_order(agent_id, order_id)


def set_pending(agent_id: str, order_id: str, pending_id: str, status: str) -> dict[str, Any]:
    get_order(agent_id, order_id)
    normalized = str(status or "").strip().lower()
    if normalized not in {"open", "done"}:
        raise ValueError("Status da pendência deve ser open ou done")
    with connect() as conn:
        cur = conn.execute(
            "UPDATE logistics_pending SET status=?,updated_at=? WHERE id=? AND order_id=?",
            (normalized, _now(), pending_id, order_id),
        )
        if cur.rowcount != 1:
            raise ValueError("Pendência não encontrada")
    return get_order(agent_id, order_id)


def build_logistics_tools(agent_id: str, *, source: str = "user") -> list[object]:
    def logistics_board() -> str:
        """Leia o Kanban logístico real da empresa, incluindo prazos e pendências."""
        return json.dumps(board_snapshot(agent_id), ensure_ascii=False, default=str)

    def logistics_create_order(customer: str, title: str, reference: str = "", promised_date: str = "",
                               priority: str = "normal", notes: str = "") -> str:
        """Crie um pedido no ERP logístico. Datas usam YYYY-MM-DD."""
        return json.dumps(create_order(agent_id, {
            "customer": customer, "title": title, "reference": reference, "promised_date": promised_date,
            "priority": priority, "notes": notes,
        }, source=source), ensure_ascii=False, default=str)

    def logistics_update_order(order_id: str, stage: str = "", promised_date: str = "",
                               scheduled_date: str = "", priority: str = "", notes: str = "") -> str:
        """Atualize etapa, prazo, entrega agendada, prioridade ou notas de um pedido existente."""
        data = {k: v for k, v in {
            "stage": stage, "promised_date": promised_date, "scheduled_date": scheduled_date,
            "priority": priority, "notes": notes,
        }.items() if v != ""}
        return json.dumps(update_order(agent_id, order_id, data), ensure_ascii=False, default=str)

    def logistics_add_pending(order_id: str, title: str, due_date: str = "") -> str:
        """Adicione uma pendência concreta a um pedido logístico."""
        return json.dumps(add_pending(agent_id, order_id, title, due_date, source=source), ensure_ascii=False, default=str)

    def logistics_set_pending(order_id: str, pending_id: str, status: str = "done") -> str:
        """Marque uma pendência como done ou reabra com open."""
        return json.dumps(set_pending(agent_id, order_id, pending_id, status), ensure_ascii=False, default=str)

    return [logistics_board, logistics_create_order, logistics_update_order, logistics_add_pending, logistics_set_pending]
