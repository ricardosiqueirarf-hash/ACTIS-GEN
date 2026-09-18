"""Persistent Slack-like coordination channels for ACTIS GEN agents."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from meuharness.event_bus import emit_event
from meuharness.storage import connect, init_db
from meuharness.tenant import (
    assert_organization_access,
    current_organization_id,
    filter_by_organization,
    resolve_inherited_organization_id,
)

VALID_KINDS = {"message", "decision", "goal", "rule", "note"}
VALID_STATUS = {"active", "done", "archived"}
VALID_SCOPES = {"global", "company", "sector", "agent", "members"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_channels_schema() -> None:
    init_db()
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_channels (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT 'global',
            scope_id TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_channel_members (
            channel_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(channel_id, agent_id),
            FOREIGN KEY(channel_id) REFERENCES agent_channels(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS agent_channel_messages (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            author_type TEXT NOT NULL DEFAULT 'user',
            author_id TEXT,
            author_name TEXT NOT NULL DEFAULT 'Você',
            kind TEXT NOT NULL DEFAULT 'message',
            content TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            long_term INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(channel_id) REFERENCES agent_channels(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_agent_channel_messages_channel_created
        ON agent_channel_messages(channel_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agent_channel_messages_longterm
        ON agent_channel_messages(long_term, pinned, status, created_at DESC);
        """)
        _ensure_organization_columns(conn)
    ensure_default_channel()


def _ensure_organization_columns(conn) -> None:
    for table in ("agent_channels", "agent_channel_messages"):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "organization_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN organization_id TEXT")


def ensure_default_channel() -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM agent_channels WHERE name='#general' AND scope_type='global' LIMIT 1"
        ).fetchone()
        if row:
            return
        now = _now()
        conn.execute(
            "INSERT INTO agent_channels(id,name,topic,scope_type,scope_id,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "#general", "Diretrizes e decisões globais de longo prazo.", "global", None, None, now, now),
        )


def _assert_channel_access(channel: dict[str, Any]) -> None:
    organization_id = current_organization_id()
    if organization_id:
        assert_organization_access(channel, organization_id, resource="Canal", allow_unscoped=True)


def _resolve_channel_organization(data: dict[str, Any], existing: dict[str, Any] | None = None) -> str | None:
    return resolve_inherited_organization_id(data.get("organization_id"), existing)


def list_channels(*, include_archived: bool = False) -> list[dict[str, Any]]:
    init_channels_schema()
    where = "" if include_archived else "WHERE c.archived=0"
    with connect() as conn:
        rows = conn.execute(f"""
            SELECT c.*, COUNT(DISTINCT m.agent_id) AS member_count,
                   COUNT(DISTINCT CASE WHEN x.long_term=1 AND x.status='active' THEN x.id END) AS long_term_count,
                   MAX(x.created_at) AS last_message_at
            FROM agent_channels c
            LEFT JOIN agent_channel_members m ON m.channel_id=c.id
            LEFT JOIN agent_channel_messages x ON x.channel_id=c.id
            {where}
            GROUP BY c.id
            ORDER BY c.archived, COALESCE(MAX(x.created_at),c.updated_at) DESC
        """).fetchall()
    return [dict(row) for row in filter_by_organization([dict(row) for row in rows], include_global=True)]

def get_channel(channel_id: str) -> dict[str, Any] | None:
    init_channels_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_channels WHERE id=?", (channel_id,)).fetchone()
        if not row:
            return None
        channel = dict(row)
        _assert_channel_access(channel)
        members = conn.execute(
            "SELECT agent_id FROM agent_channel_members WHERE channel_id=? ORDER BY agent_id",
            (channel_id,),
        ).fetchall()
    channel["members"] = [str(item["agent_id"]) for item in members]
    return channel


def create_channel(data: dict[str, Any]) -> dict[str, Any]:
    init_channels_schema()
    name = str(data.get("name") or "").strip()
    topic = str(data.get("topic") or "").strip()
    scope_type = str(data.get("scope_type") or "global").strip().lower()
    scope_id = str(data.get("scope_id") or "").strip() or None
    if not name:
        raise ValueError("Nome do canal é obrigatório.")
    if not name.startswith("#"):
        name = "#" + name
    if scope_type not in VALID_SCOPES:
        raise ValueError("Escopo do canal inválido.")
    if scope_type not in {"global", "members"} and not scope_id:
        raise ValueError("Canal com escopo específico precisa de scope_id.")
    existing = None
    organization_id = _resolve_channel_organization(data, existing)
    now = _now()
    channel_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_channels(id,name,topic,scope_type,scope_id,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (channel_id, name[:80], topic[:500], scope_type, scope_id, organization_id, now, now),
        )
    set_channel_members(channel_id, list(data.get("members") or []))
    emit_event("channel.created", channel_id=channel_id, name=name, scope_type=scope_type, scope_id=scope_id)
    return get_channel(channel_id) or {}


def set_channel_members(channel_id: str, agent_ids: list[str]) -> list[str]:
    init_channels_schema()
    if not get_channel(channel_id):
        raise ValueError("Canal não encontrado.")
    clean = [str(value).strip() for value in agent_ids if str(value).strip()]
    clean = list(dict.fromkeys(clean))
    now = _now()
    with connect() as conn:
        conn.execute("DELETE FROM agent_channel_members WHERE channel_id=?", (channel_id,))
        conn.executemany(
            "INSERT INTO agent_channel_members(channel_id,agent_id,created_at) VALUES(?,?,?)",
            [(channel_id, agent_id, now) for agent_id in clean],
        )
        conn.execute("UPDATE agent_channels SET updated_at=? WHERE id=?", (now, channel_id))
    emit_event("channel.members.updated", channel_id=channel_id, members=clean)
    return clean


def list_channel_messages(channel_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    init_channels_schema()
    if not get_channel(channel_id):
        raise ValueError("Canal não encontrado.")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_channel_messages WHERE channel_id=? ORDER BY created_at ASC LIMIT ?",
            (channel_id, max(1, min(int(limit), 1000))),
        ).fetchall()
    return [dict(row) for row in filter_by_organization([dict(row) for row in rows], include_global=True)]

def create_channel_message(channel_id: str, data: dict[str, Any]) -> dict[str, Any]:
    init_channels_schema()
    channel = get_channel(channel_id)
    if not channel:
        raise ValueError("Canal não encontrado.")
    content = str(data.get("content") or "").strip()
    if not content:
        raise ValueError("Mensagem vazia.")
    kind = str(data.get("kind") or "message").strip().lower()
    if kind not in VALID_KINDS:
        raise ValueError("Tipo de mensagem inválido.")
    status = str(data.get("status") or "active").strip().lower()
    if status not in VALID_STATUS:
        raise ValueError("Status inválido.")
    pinned = bool(data.get("pinned", False))
    long_term = bool(data.get("long_term", False)) or kind in {"decision", "goal", "rule"}
    organization_id = _resolve_channel_organization(data, channel)
    now = _now()
    item = {
        "id": uuid.uuid4().hex,
        "channel_id": channel_id,
        "author_type": str(data.get("author_type") or "user")[:20],
        "author_id": str(data.get("author_id") or "").strip() or None,
        "author_name": str(data.get("author_name") or "Você").strip()[:120] or "Você",
        "kind": kind,
        "content": content,
        "pinned": int(pinned),
        "long_term": int(long_term),
        "status": status,
        "created_at": now,
        "updated_at": now,
        "organization_id": organization_id,
    }
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_channel_messages
            (id,channel_id,author_type,author_id,author_name,kind,content,pinned,long_term,status,created_at,updated_at,organization_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            item["id"], channel_id, item["author_type"], item["author_id"], item["author_name"],
            kind, content, item["pinned"], item["long_term"], status, now, now, organization_id,
        ))
        conn.execute("UPDATE agent_channels SET updated_at=? WHERE id=?", (now, channel_id))
    emit_event(
        "channel.message.created",
        channel_id=channel_id,
        message_id=item["id"],
        kind=kind,
        long_term=bool(item["long_term"]),
        author_id=item["author_id"],
    )
    return item


def update_channel_message(message_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    init_channels_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_channel_messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            return None
        existing = dict(row)
        _assert_channel_access(existing)
        organization_id = _resolve_channel_organization(data, existing)
    changes: list[str] = []
    params: list[Any] = []
    if "pinned" in data:
        changes.append("pinned=?")
        params.append(int(bool(data.get("pinned"))))
    if "long_term" in data:
        changes.append("long_term=?")
        params.append(int(bool(data.get("long_term"))))
    if "status" in data:
        status = str(data.get("status") or "active").strip().lower()
        if status not in VALID_STATUS:
            raise ValueError("Status inválido.")
        changes.append("status=?")
        params.append(status)
    if not changes:
        return None
    changes.extend(("organization_id=?", "updated_at=?"))
    params.extend((organization_id, _now()))
    params.append(message_id)
    with connect() as conn:
        conn.execute(f"UPDATE agent_channel_messages SET {', '.join(changes)} WHERE id=?", params)
        row = conn.execute("SELECT * FROM agent_channel_messages WHERE id=?", (message_id,)).fetchone()
    result = dict(row) if row else None
    if result:
        emit_event(
            "channel.message.updated", channel_id=result["channel_id"], message_id=message_id,
            pinned=bool(result["pinned"]), long_term=bool(result["long_term"]), status=result["status"],
        )
    return result


def delete_channel_message(message_id: str) -> bool:
    init_channels_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_channel_messages WHERE id=?", (message_id,)
        ).fetchone()
        if not row:
            return False
        _assert_channel_access(dict(row))
        channel_id = str(row["channel_id"])
        cursor = conn.execute("DELETE FROM agent_channel_messages WHERE id=?", (message_id,))
        if cursor.rowcount:
            conn.execute("UPDATE agent_channels SET updated_at=? WHERE id=?", (_now(), channel_id))
    if cursor.rowcount:
        emit_event("channel.message.deleted", channel_id=channel_id, message_id=message_id)
    return bool(cursor.rowcount)


def delete_channel(channel_id: str) -> bool:
    init_channels_schema()
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_channels WHERE id=?", (channel_id,)).fetchone()
        if not row:
            return False
        channel = dict(row)
        _assert_channel_access(channel)
        if channel.get("name") == "#general":
            return False
        cursor = conn.execute("DELETE FROM agent_channels WHERE id=?", (channel_id,))
    return bool(cursor.rowcount)

def _channel_applies(channel: dict[str, Any], agent: dict[str, Any], members: set[str]) -> bool:
    agent_id = str(agent.get("id") or "")
    if agent_id in members:
        return True
    scope_type = str(channel.get("scope_type") or "global")
    scope_id = str(channel.get("scope_id") or "")
    if scope_type == "global":
        return True
    if scope_type == "members":
        return agent_id in members
    if scope_type == "company":
        return scope_id == str(agent.get("company_id") or "")
    if scope_type == "sector":
        return scope_id == str(agent.get("sector_id") or "")
    if scope_type == "agent":
        return scope_id == agent_id
    return False


def channel_context_for_agent(agent: dict[str, Any], prompt: str = "", *, limit: int = 16) -> list[dict[str, Any]]:
    """Long-term channel items visible to an agent. Pinned items always win."""
    init_channels_schema()
    channels = list_channels()
    with connect() as conn:
        member_rows = conn.execute("SELECT channel_id,agent_id FROM agent_channel_members").fetchall()
        membership: dict[str, set[str]] = {}
        for row in member_rows:
            membership.setdefault(str(row["channel_id"]), set()).add(str(row["agent_id"]))
        allowed = [c for c in channels if _channel_applies(c, agent, membership.get(str(c["id"]), set()))]
        if not allowed:
            return []
        ids = [str(c["id"]) for c in allowed]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"""
            SELECT m.*, c.name AS channel_name
            FROM agent_channel_messages m
            JOIN agent_channels c ON c.id=m.channel_id
            WHERE m.channel_id IN ({placeholders}) AND m.long_term=1 AND m.status='active'
            ORDER BY m.pinned DESC, m.updated_at DESC
        """, ids).fetchall()
    query = {part.lower() for part in str(prompt or "").replace("/", " ").split() if len(part) >= 4}
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        hay = str(item.get("content") or "").lower()
        overlap = sum(1 for term in query if term in hay)
        weight = 100 if item.get("pinned") else 0
        weight += {"rule": 35, "decision": 30, "goal": 25, "note": 10, "message": 0}.get(str(item.get("kind")), 0)
        weight += overlap * 8
        scored.append((weight, item))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("updated_at") or "")), reverse=True)
    return [item for _, item in scored[:max(1, min(int(limit), 40))]]


def channel_context_instructions(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["\n\nCANAIS ACTIS — DIRETRIZES DE LONGO PRAZO:"]
    for item in items:
        marker = "FIXADO" if item.get("pinned") else str(item.get("kind") or "note").upper()
        lines.append(f"- [{item.get('channel_name')} · {marker}] {item.get('content')}")
    lines.append(
        "Trate estes itens como contexto persistente de coordenação. Mensagens atuais do usuário e regras mais específicas têm precedência em caso de conflito."
    )
    return "\n".join(lines)
