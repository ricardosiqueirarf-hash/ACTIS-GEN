"""Lightweight WhatsApp CRM for the ACTIS WhatsApp shadow database."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from meuharness.agents import get_agent
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.storage import connect

STAGES = {"novo", "qualificando", "orcamento", "aguardando_cliente", "fechado", "producao", "entregue", "perdido"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def init_crm_schema() -> None:
    from meuharness.domains.whatsapp_shadow import init_whatsapp_schema
    init_whatsapp_schema()
    with connect() as conn:        conn.executescript("""
        CREATE TABLE IF NOT EXISTS whatsapp_crm_conversations (
            conversation_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL DEFAULT 'novo',
            owner TEXT,
            next_action TEXT NOT NULL DEFAULT '',
            ai_summary TEXT NOT NULL DEFAULT '',
            lead_value REAL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            ai_updated_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS whatsapp_crm_orders (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            title TEXT NOT NULL DEFAULT '',
            kind TEXT,
            environment TEXT,
            specs_json TEXT NOT NULL DEFAULT '{}',
            value REAL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_crm_orders_conv ON whatsapp_crm_orders(conversation_id, updated_at DESC);        CREATE TABLE IF NOT EXISTS whatsapp_crm_tasks (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            due_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_crm_tasks_conv ON whatsapp_crm_tasks(conversation_id, status, updated_at DESC);
        CREATE TABLE IF NOT EXISTS whatsapp_crm_pending (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_crm_pending_conv ON whatsapp_crm_pending(conversation_id, status, updated_at DESC);
        """)


def _conversation(agent_id: str, contact_name: str) -> dict[str, Any] | None:
    from meuharness.domains.whatsapp_shadow import get_conversation_by_contact
    return get_conversation_by_contact(agent_id, contact_name)
def _ensure_crm(conversation_id: str) -> dict[str, Any]:
    init_crm_schema()
    now = _now()
    with connect() as conn:
        conn.execute("""INSERT OR IGNORE INTO whatsapp_crm_conversations
            (conversation_id,updated_at) VALUES(?,?)""", (conversation_id, now))
        row = conn.execute("SELECT * FROM whatsapp_crm_conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
    return dict(row)


def crm_snapshot(agent_id: str, contact_name: str) -> dict[str, Any]:
    init_crm_schema()
    conv = _conversation(agent_id, contact_name)
    if not conv:
        raise ValueError("Conversa não encontrada no Shadow DB")
    crm = _ensure_crm(conv["id"])
    with connect() as conn:
        orders = [dict(r) for r in conn.execute("SELECT * FROM whatsapp_crm_orders WHERE conversation_id=? ORDER BY updated_at DESC", (conv["id"],)).fetchall()]
        tasks = [dict(r) for r in conn.execute("SELECT * FROM whatsapp_crm_tasks WHERE conversation_id=? ORDER BY status,updated_at DESC", (conv["id"],)).fetchall()]
        pending = [dict(r) for r in conn.execute("SELECT * FROM whatsapp_crm_pending WHERE conversation_id=? ORDER BY status,updated_at DESC", (conv["id"],)).fetchall()]
    crm["tags"] = _json_load(crm.pop("tags_json", "[]"), [])
    for order in orders:
        order["specs"] = _json_load(order.pop("specs_json", "{}"), {})
    return {"conversation": conv, "crm": crm, "orders": orders, "tasks": tasks, "pending": pending}


def crm_inbox(agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from meuharness.domains.whatsapp_shadow import list_inbox
    rows = list_inbox(agent_id, limit=limit)
    result = []
    for row in rows:
        crm = _ensure_crm(row["id"])
        item = dict(row)
        item["crm_stage"] = crm.get("stage") or "novo"
        item["crm_next_action"] = crm.get("next_action") or ""
        item["crm_summary"] = crm.get("ai_summary") or ""
        item["crm_value"] = crm.get("lead_value")
        item["crm_owner"] = crm.get("owner")
        result.append(item)
    return result


def update_crm(agent_id: str, contact_name: str, data: dict[str, Any]) -> dict[str, Any]:
    conv = _conversation(agent_id, contact_name)
    if not conv:
        raise ValueError("Conversa não encontrada")
    _ensure_crm(conv["id"])
    allowed = {"stage", "owner", "next_action", "lead_value"}
    values: dict[str, Any] = {k: data[k] for k in allowed if k in data}
    if "stage" in values and values["stage"] not in STAGES:
        raise ValueError("Stage inválido")
    fields = []
    params = []
    for key, value in values.items():
        fields.append(f"{key}=?")
        params.append(value)
    if fields:
        fields.append("updated_at=?")
        params.extend([_now(), conv["id"]])
        with connect() as conn:
            conn.execute(f"UPDATE whatsapp_crm_conversations SET {','.join(fields)} WHERE conversation_id=?", params)
    return crm_snapshot(agent_id, contact_name)


def add_item(agent_id: str, contact_name: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    conv = _conversation(agent_id, contact_name)
    if not conv:
        raise ValueError("Conversa não encontrada")
    init_crm_schema()
    now, item_id = _now(), uuid.uuid4().hex
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Título obrigatório")
    with connect() as conn:
        if kind == "task":
            conn.execute("""INSERT INTO whatsapp_crm_tasks
                (id,conversation_id,source,title,status,due_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                (item_id, conv["id"], "user", title, "open", data.get("due_at"), now, now))
        elif kind == "pending":
            conn.execute("""INSERT INTO whatsapp_crm_pending
                (id,conversation_id,source,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                (item_id, conv["id"], "user", title, "open", now, now))
        elif kind == "order":
            conn.execute("""INSERT INTO whatsapp_crm_orders
                (id,conversation_id,source,title,kind,environment,specs_json,value,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, conv["id"], "user", title, data.get("kind"), data.get("environment"),
                 json.dumps(data.get("specs") or {}, ensure_ascii=False), data.get("value"), data.get("status") or "draft", now, now))
        else:
            raise ValueError("Tipo de item inválido")
    return crm_snapshot(agent_id, contact_name)


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("A IA não retornou JSON válido")
    value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Resposta de IA inválida")
    return value


def _triage_prompt(contact_name: str, messages: list[dict[str, Any]]) -> str:
    compact = [{"direction": m.get("direction"), "sender": m.get("sender"), "content": m.get("content"), "ts": m.get("message_ts")} for m in messages[-80:]]
    return """Você organiza uma inbox WhatsApp B2B em CRM. Analise SOMENTE as mensagens fornecidas. Não invente preço, medida, produto, prazo ou intenção. Retorne APENAS JSON válido com esta estrutura:
{"stage":"novo|qualificando|orcamento|aguardando_cliente|fechado|producao|entregue|perdido","summary":"resumo operacional curto","next_action":"próxima ação concreta","priority":"low|normal|high|urgent","intent":"tipo principal da demanda","lead_value":null,"order":null,"tasks":[],"pending":[]}
Se houver pedido claro, order={"title":"...","kind":null,"environment":null,"specs":{},"value":null,"status":"draft"}. tasks e pending são listas de strings, no máximo 4 itens cada. Se não houver evidência, use null/lista vazia. Contato: """ + json.dumps(contact_name, ensure_ascii=False) + "\nMensagens:\n" + json.dumps(compact, ensure_ascii=False)
def triage_conversation(agent_id: str, contact_name: str, *, env_file: str | None = None) -> dict[str, Any]:
    from meuharness.domains.whatsapp_shadow import recent_messages
    conv = _conversation(agent_id, contact_name)
    if not conv:
        raise ValueError("Conversa não encontrada")
    messages = list(reversed(recent_messages(agent_id, contact_name=contact_name, limit=120)))
    if not messages:
        raise ValueError("Sem mensagens para organizar")
    agent = get_agent(agent_id) or {}
    settings = NineRouterSettings.from_env(env_file, model=str(agent.get("model") or "").strip() or None, timeout_s=45)
    result = asyncio.run(NineRouterGateway(settings).complete(_triage_prompt(contact_name, messages), max_output_tokens=900))
    data = _extract_json(result.text)
    stage = str(data.get("stage") or "novo")
    if stage not in STAGES:
        stage = "novo"
    summary = str(data.get("summary") or "").strip()[:1600]
    next_action = str(data.get("next_action") or "").strip()[:500]
    priority = str(data.get("priority") or "normal")
    if priority not in {"low", "normal", "high", "urgent"}:
        priority = "normal"
    intent = str(data.get("intent") or "").strip()[:120] or None
    lead_value = data.get("lead_value") if isinstance(data.get("lead_value"), (int, float)) else None
    now = _now()
    _ensure_crm(conv["id"])
    with connect() as conn:
        conn.execute("""UPDATE whatsapp_crm_conversations SET stage=?,next_action=?,ai_summary=?,lead_value=?,ai_updated_at=?,updated_at=? WHERE conversation_id=?""",
                     (stage, next_action, summary, lead_value, now, now, conv["id"]))
        conn.execute("UPDATE whatsapp_conversations SET priority=?,intent=?,summary=?,updated_at=? WHERE id=?",
                     (priority, intent, summary, now, conv["id"]))
        conn.execute("DELETE FROM whatsapp_crm_tasks WHERE conversation_id=? AND source='ai'", (conv["id"],))
        conn.execute("DELETE FROM whatsapp_crm_pending WHERE conversation_id=? AND source='ai'", (conv["id"],))
        conn.execute("DELETE FROM whatsapp_crm_orders WHERE conversation_id=? AND source='ai'", (conv["id"],))
        for title in list(data.get("tasks") or [])[:4]:
            title = str(title).strip()
            if title:
                conn.execute("""INSERT INTO whatsapp_crm_tasks
                    (id,conversation_id,source,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, conv["id"], "ai", title[:500], "open", now, now))
        for title in list(data.get("pending") or [])[:4]:
            title = str(title).strip()
            if title:
                conn.execute("""INSERT INTO whatsapp_crm_pending
                    (id,conversation_id,source,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, conv["id"], "ai", title[:500], "open", now, now))
        order = data.get("order")
        if isinstance(order, dict) and str(order.get("title") or "").strip():
            conn.execute("""INSERT INTO whatsapp_crm_orders
                (id,conversation_id,source,title,kind,environment,specs_json,value,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, conv["id"], "ai", str(order.get("title"))[:500], order.get("kind"),
                 order.get("environment"), json.dumps(order.get("specs") or {}, ensure_ascii=False),
                 order.get("value") if isinstance(order.get("value"), (int, float)) else None,
                 str(order.get("status") or "draft")[:80], now, now))
    snapshot = crm_snapshot(agent_id, contact_name)
    snapshot["ai"] = {"model": result.model, "raw": data}
    return snapshot
