"""Local-first WhatsApp shadow state for ACTIS GEN.

WhatsApp Web is an external source/transport. This module stores the operational
copy used by agents so ordinary recall does not require browser navigation.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from meuharness.event_bus import emit_event
from meuharness.storage import connect, init_db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize_message_timestamp(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        datetime.fromisoformat(raw)
        return raw
    except ValueError:
        pass
    match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})(T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)",
        raw,
    )
    if not match:
        return raw
    year, middle, last, suffix = match.groups()
    month, day = int(middle), int(last)
    if month > 12 and 1 <= day <= 12:
        month, day = day, month
    candidate = f"{int(year):04d}-{month:02d}-{day:02d}{suffix}"
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return candidate


def _message_datetime(value: Any) -> datetime | None:
    """Parse stored/WhatsApp timestamps to one comparable instant.

    WhatsApp Web emits browser-local ISO timestamps without an offset. Those
    must be interpreted in the machine's local timezone before comparison with
    UTC/offset-aware timestamps already present in the shadow DB.
    """
    normalized = _normalize_message_timestamp(value)
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            local_tz = UTC
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(UTC)


def _timestamp_is_newer(candidate: Any, current: Any) -> bool:
    candidate_dt = _message_datetime(candidate)
    current_dt = _message_datetime(current)
    if candidate_dt is None:
        return False
    if current_dt is None:
        return True
    return candidate_dt > current_dt


def _timestamp_is_not_older(candidate: Any, current: Any) -> bool:
    """True when a newly inserted message is at least as recent as current state.

    WhatsApp Web frequently exposes only minute precision. Two genuinely new
    messages received in the same minute therefore share the exact timestamp;
    equality must not suppress the second inbound event.
    """
    candidate_dt = _message_datetime(candidate)
    current_dt = _message_datetime(current)
    if candidate_dt is None:
        return True
    if current_dt is None:
        return True
    return candidate_dt >= current_dt


def _latest_timestamp(*values: Any) -> str | None:
    best_value: str | None = None
    best_dt: datetime | None = None
    for value in values:
        normalized = _normalize_message_timestamp(value)
        if not normalized:
            continue
        parsed = _message_datetime(normalized)
        if parsed is None:
            continue
        if best_dt is None or parsed > best_dt:
            best_value, best_dt = normalized, parsed
    return best_value


def _ensure_column(conn: Any, table: str, column: str, ddl: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_whatsapp_schema() -> None:
    init_db()
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS whatsapp_contacts (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            external_id TEXT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'contact',            monitored INTEGER NOT NULL DEFAULT 1,
            sync_history INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(agent_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_wa_contacts_agent_monitored
        ON whatsapp_contacts(agent_id, monitored, name);

        CREATE TABLE IF NOT EXISTS whatsapp_conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            contact_id TEXT NOT NULL,
            title TEXT NOT NULL,
            last_message_at TEXT,
            last_synced_at TEXT,
            summary TEXT NOT NULL DEFAULT '',
            sync_status TEXT NOT NULL DEFAULT 'never',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(agent_id, contact_id),
            FOREIGN KEY(contact_id) REFERENCES whatsapp_contacts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_conversations_agent
        ON whatsapp_conversations(agent_id, last_message_at DESC);

        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            external_id TEXT,
            fingerprint TEXT NOT NULL,            direction TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            message_type TEXT NOT NULL DEFAULT 'text',
            message_ts TEXT,
            reply_to TEXT,
            attachment_path TEXT,
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(conversation_id, fingerprint),
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_messages_conversation_ts
        ON whatsapp_messages(conversation_id, message_ts DESC, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wa_messages_external
        ON whatsapp_messages(conversation_id, external_id)
        WHERE external_id IS NOT NULL AND external_id <> '';
        CREATE VIRTUAL TABLE IF NOT EXISTS whatsapp_messages_fts USING fts5(
            message_id UNINDEXED, conversation_id UNINDEXED, content, sender
        );

        CREATE TABLE IF NOT EXISTS whatsapp_conversation_state (
            conversation_id TEXT PRIMARY KEY,
            current_subject TEXT NOT NULL DEFAULT '',
            pending_items TEXT NOT NULL DEFAULT '[]',
            last_customer_request TEXT NOT NULL DEFAULT '',
            last_agent_action TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS whatsapp_sync_state (
            conversation_id TEXT PRIMARY KEY,
            last_external_id TEXT,
            last_message_ts TEXT,
            last_success_at TEXT,
            status TEXT NOT NULL DEFAULT 'never',
            error TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS whatsapp_outbox (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            content TEXT NOT NULL,
            message_type TEXT NOT NULL DEFAULT 'text',
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            error TEXT,
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_outbox_status
        ON whatsapp_outbox(agent_id, status, created_at);

        CREATE TABLE IF NOT EXISTS whatsapp_labels (
            conversation_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, name),
            FOREIGN KEY(conversation_id) REFERENCES whatsapp_conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wa_labels_name ON whatsapp_labels(name, conversation_id);

        CREATE TABLE IF NOT EXISTS whatsapp_runtime_settings (
            agent_id TEXT PRIMARY KEY,
            automation_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        """)
        _ensure_column(conn, "whatsapp_messages", "quarantined", "INTEGER NOT NULL DEFAULT 0")
        for column, ddl in (
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("assigned_agent", "TEXT"),
            ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
            ("unread_count", "INTEGER NOT NULL DEFAULT 0"),
            ("first_inbound_at", "TEXT"),
            ("last_customer_at", "TEXT"),
            ("last_agent_at", "TEXT"),
            ("resolved_at", "TEXT"),
            ("automation_mode", "TEXT NOT NULL DEFAULT 'supervised'"),
            ("intent", "TEXT"),
            ("intent_confidence", "REAL"),
            ("language", "TEXT"),
        ):
            _ensure_column(conn, "whatsapp_conversations", column, ddl)
        for column, ddl in (
            ("oldest_external_id", "TEXT"),
            ("newest_external_id", "TEXT"),
            ("oldest_message_ts", "TEXT"),
            ("newest_message_ts", "TEXT"),
            ("anchor_external_id", "TEXT"),
            ("overlap_count", "INTEGER NOT NULL DEFAULT 0"),
            ("backfill_complete", "INTEGER NOT NULL DEFAULT 0"),
            ("gap_suspected", "INTEGER NOT NULL DEFAULT 0"),
            ("last_complete_scan_at", "TEXT"),
        ):
            _ensure_column(conn, "whatsapp_sync_state", column, ddl)
        for column, ddl in (
            ("updated_at", "TEXT"),
            ("last_attempt_at", "TEXT"),
            ("verified_at", "TEXT"),
            ("transport_ref", "TEXT"),
            ("origin_source", "TEXT NOT NULL DEFAULT 'user'"),
            ("idempotency_key", "TEXT"),
        ):
            _ensure_column(conn, "whatsapp_outbox", column, ddl)
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_wa_outbox_idempotency
                      ON whatsapp_outbox(agent_id,idempotency_key)
                      WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''""")


def upsert_contact(agent_id: str, name: str, *, external_id: str | None = None,
                   kind: str = "contact", monitored: bool = True,
                   sync_history: bool = False) -> dict[str, Any]:
    init_whatsapp_schema()
    agent_id, name = str(agent_id).strip(), str(name).strip()
    if not agent_id or not name:
        raise ValueError("agent_id e name são obrigatórios")
    from meuharness.domains.whatsapp_transport import normalize_phone
    if external_id is None and kind == "contact":
        external_id = normalize_phone(name) or None
    now = _now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM whatsapp_contacts WHERE agent_id=? AND name=?",
            (agent_id, name),
        ).fetchone()
        contact_id = str(row["id"]) if row else uuid.uuid4().hex
        conn.execute("""
            INSERT INTO whatsapp_contacts
            (id,agent_id,external_id,name,kind,monitored,sync_history,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_id,name) DO UPDATE SET
              external_id=COALESCE(excluded.external_id, whatsapp_contacts.external_id),
              kind=excluded.kind, monitored=excluded.monitored,
              sync_history=excluded.sync_history, updated_at=excluded.updated_at
        """, (contact_id, agent_id, external_id, name, kind, int(monitored),
              int(sync_history), now, now))
        row = conn.execute("SELECT * FROM whatsapp_contacts WHERE agent_id=? AND name=?", (agent_id, name)).fetchone()
        return dict(row) if row else {}

def get_contact(agent_id: str, name: str) -> dict[str, Any] | None:
    init_whatsapp_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_contacts WHERE agent_id=? AND name=?",
            (agent_id, name),
        ).fetchone()
    return dict(row) if row else None


def list_monitored_contacts(agent_id: str) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM whatsapp_contacts WHERE agent_id=? AND monitored=1 ORDER BY name",
            (agent_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_conversation(agent_id: str, contact_id: str, title: str) -> dict[str, Any]:
    init_whatsapp_schema()
    now = _now()
    conversation_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute("""
            INSERT INTO whatsapp_conversations
            (id,agent_id,contact_id,title,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(agent_id,contact_id) DO UPDATE SET
              title=excluded.title, updated_at=excluded.updated_at
        """, (conversation_id, agent_id, contact_id, title, now, now))
        row = conn.execute(
            "SELECT * FROM whatsapp_conversations WHERE agent_id=? AND contact_id=?",
            (agent_id, contact_id),
        ).fetchone()
    return dict(row)


def _fingerprint(message: dict[str, Any]) -> str:
    base = "|".join([
        str(message.get("external_id") or ""),
        str(message.get("direction") or ""),
        str(message.get("sender") or ""),
        str(message.get("timestamp") or ""),
        str(message.get("content") or ""),
    ])
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


def ingest_messages(agent_id: str, contact_name: str,
                    messages: Iterable[dict[str, Any]], *, emit_inbound_events: bool = True, verified_contact: bool = False) -> dict[str, Any]:
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    inserted = 0
    newest_ts: str | None = None
    newest_external: str | None = None
    incoming_inserted = 0
    last_customer_at: str | None = None
    last_agent_at: str | None = None
    emitted: list[dict[str, Any]] = []
    now = _now()
    with connect() as conn:
        for message in messages:
            external_id = str(message.get("external_id") or "").strip() or None
            content = str(message.get("content") or "")
            sender = str(message.get("sender") or "")
            direction = str(message.get("direction") or "incoming")
            timestamp = _normalize_message_timestamp(message.get("timestamp"))
            effective_ts = timestamp or (now if emit_inbound_events else None)
            fingerprint = _fingerprint(message)
            message_id = uuid.uuid4().hex
            cursor = conn.execute("""
                INSERT OR IGNORE INTO whatsapp_messages
                (id,conversation_id,external_id,fingerprint,direction,sender,content,
                 message_type,message_ts,reply_to,attachment_path,raw_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (message_id, conversation["id"], external_id, fingerprint, direction,
                  sender, content, str(message.get("message_type") or "text"), timestamp,
                  message.get("reply_to"), message.get("attachment_path"), _json(message), now))
            was_inserted = bool(cursor.rowcount)
            if verified_contact and external_id:
                conn.execute("""UPDATE whatsapp_messages SET direction=?,sender=?,content=?,message_type=?,
                    message_ts=?,raw_json=?,quarantined=0 WHERE conversation_id=? AND external_id=?""",
                    (direction,sender,content,str(message.get("message_type") or "text"),timestamp,
                     _json(message),conversation["id"],external_id))
                conn.execute("""UPDATE whatsapp_messages SET quarantined=1 WHERE external_id=?
                    AND conversation_id<>? AND conversation_id IN
                    (SELECT id FROM whatsapp_conversations WHERE agent_id=?)""",
                    (external_id,conversation["id"],agent_id))
                if not was_inserted:
                    conn.execute("""UPDATE whatsapp_messages_fts SET content=?,sender=? WHERE message_id IN
                        (SELECT id FROM whatsapp_messages WHERE conversation_id=? AND external_id=?)""",
                        (content,sender,conversation["id"],external_id))
            if was_inserted:
                conn.execute(
                    "INSERT INTO whatsapp_messages_fts(message_id,conversation_id,content,sender) VALUES(?,?,?,?)",
                    (message_id, conversation["id"], content, sender),
                )
                inserted += 1
                if direction == "incoming":
                    incoming_inserted += 1
                    if effective_ts:
                        last_customer_at = _latest_timestamp(last_customer_at, effective_ts)
                    if not timestamp or _timestamp_is_not_older(timestamp, conversation.get("last_message_at")):
                        emitted.append({
                        "message_id": message_id,
                        "external_id": external_id,
                        "content": content,
                        "sender": sender,
                        "timestamp": timestamp,
                    })
                elif direction == "outgoing":
                    if effective_ts:
                        last_agent_at = _latest_timestamp(last_agent_at, effective_ts)
            if timestamp and (newest_ts is None or _timestamp_is_newer(timestamp, newest_ts)):
                newest_ts, newest_external = timestamp, external_id
        unread_increment = len(emitted) if emit_inbound_events else 0
        final_last_message = _latest_timestamp(conversation.get("last_message_at"), newest_ts)
        final_last_customer = _latest_timestamp(conversation.get("last_customer_at"), last_customer_at)
        final_last_agent = _latest_timestamp(conversation.get("last_agent_at"), last_agent_at)
        conn.execute("""
            UPDATE whatsapp_conversations
            SET last_message_at=COALESCE(?,last_message_at),
                last_synced_at=?, sync_status='healthy', updated_at=?,
                unread_count=unread_count+?,
                first_inbound_at=COALESCE(first_inbound_at,?),
                last_customer_at=COALESCE(?,last_customer_at),
                last_agent_at=COALESCE(?,last_agent_at)
            WHERE id=?
        """, (final_last_message, now, now, unread_increment, last_customer_at,
              final_last_customer, final_last_agent, conversation["id"]))
        previous_sync = conn.execute(
            "SELECT last_external_id,last_message_ts FROM whatsapp_sync_state WHERE conversation_id=?",
            (conversation["id"],),
        ).fetchone()
        previous_sync_ts = previous_sync["last_message_ts"] if previous_sync else None
        final_sync_ts = _latest_timestamp(previous_sync_ts, newest_ts)
        final_sync_external = (
            newest_external
            if newest_ts and final_sync_ts == newest_ts
            else (previous_sync["last_external_id"] if previous_sync else newest_external)
        )
        conn.execute("""INSERT INTO whatsapp_sync_state(conversation_id,last_external_id,last_message_ts,last_success_at,status,error,updated_at)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET
                      last_external_id=COALESCE(excluded.last_external_id,whatsapp_sync_state.last_external_id),
                      last_message_ts=COALESCE(excluded.last_message_ts,whatsapp_sync_state.last_message_ts),
                      last_success_at=excluded.last_success_at,status='healthy',error=NULL,updated_at=excluded.updated_at""",
                     (conversation["id"], final_sync_external, final_sync_ts, now, "healthy", None, now))
    for item in emitted if emit_inbound_events else []:
        emit_event(
            "whatsapp.message.inbound",
            agent_id=agent_id,
            conversation_id=conversation["id"],
            contact=contact_name,
            **item,
        )
        from meuharness.domains.whatsapp_automation import enqueue_inbound
        enqueue_inbound(agent_id, contact_name, item["message_id"])
        # OpenClaw-inspired channel fan-out: the transport owner remains this
        # production agent, while optional shadow participants receive an
        # isolated copy before any LLM execution. Shadow participants have no
        # outbound WhatsApp capability.
        from meuharness.domains.whatsapp_channel_gateway import route_inbound_message
        route_inbound_message(agent_id, contact_name, item)
    return {
        "contact": contact_name,
        "conversation_id": conversation["id"],
        "inserted": inserted,
        "incoming_inserted": incoming_inserted,
        "synced_at": now,
    }

def sync_state_for_contact(agent_id: str, contact_name: str) -> dict[str, Any] | None:
    init_whatsapp_schema()
    with connect() as conn:
        row = conn.execute("""
            SELECT s.* FROM whatsapp_sync_state s
            JOIN whatsapp_conversations c ON c.id=s.conversation_id
            WHERE c.agent_id=? AND c.title=?
        """, (agent_id, contact_name)).fetchone()
    return dict(row) if row else None


def known_external_ids(agent_id: str, contact_name: str, *, limit: int = 2000) -> list[str]:
    init_whatsapp_schema()
    with connect() as conn:
        rows = conn.execute("""
            SELECT m.external_id FROM whatsapp_messages m
            JOIN whatsapp_conversations c ON c.id=m.conversation_id
            WHERE c.agent_id=? AND c.title=?
              AND m.quarantined=0 AND m.external_id IS NOT NULL AND m.external_id <> ''
            ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END,
                     m.message_ts DESC, m.created_at DESC LIMIT ?
        """, (agent_id, contact_name, max(1, min(int(limit), 10000)))).fetchall()
    return [str(row["external_id"]) for row in rows]


def set_sync_continuity(agent_id: str, contact_name: str, *, status: str,
                        anchor_external_id: str | None = None, overlap_count: int = 0,
                        gap_suspected: bool = False, backfill_complete: bool | None = None,
                        last_complete_scan: bool = False, error: str | None = None) -> dict[str, Any]:
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    now = _now()
    with connect() as conn:
        bounds = conn.execute("""
            SELECT external_id,message_ts,created_at FROM whatsapp_messages
            WHERE conversation_id=? AND quarantined=0
            ORDER BY CASE WHEN message_ts IS NULL OR message_ts='' THEN 1 ELSE 0 END,
                     message_ts ASC, created_at ASC
        """, (conversation["id"],)).fetchall()
        timestamped = [row for row in bounds if row["message_ts"]]
        edge_rows = timestamped or bounds
        oldest = dict(edge_rows[0]) if edge_rows else {}
        newest = dict(edge_rows[-1]) if edge_rows else {}
        current = conn.execute("SELECT * FROM whatsapp_sync_state WHERE conversation_id=?", (conversation["id"],)).fetchone()
        complete = int(backfill_complete if backfill_complete is not None else bool(current and current["backfill_complete"]))
        conn.execute("""
            INSERT INTO whatsapp_sync_state(
              conversation_id,last_external_id,last_message_ts,last_success_at,status,error,updated_at,
              oldest_external_id,newest_external_id,oldest_message_ts,newest_message_ts,anchor_external_id,
              overlap_count,backfill_complete,gap_suspected,last_complete_scan_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET
              last_external_id=excluded.last_external_id,last_message_ts=excluded.last_message_ts,
              last_success_at=excluded.last_success_at,status=excluded.status,error=excluded.error,updated_at=excluded.updated_at,
              oldest_external_id=excluded.oldest_external_id,newest_external_id=excluded.newest_external_id,
              oldest_message_ts=excluded.oldest_message_ts,newest_message_ts=excluded.newest_message_ts,
              anchor_external_id=excluded.anchor_external_id,overlap_count=excluded.overlap_count,
              backfill_complete=excluded.backfill_complete,gap_suspected=excluded.gap_suspected,
              last_complete_scan_at=COALESCE(excluded.last_complete_scan_at,whatsapp_sync_state.last_complete_scan_at)
        """, (conversation["id"], newest.get("external_id"), newest.get("message_ts"), now, status, error, now,
              oldest.get("external_id"), newest.get("external_id"), oldest.get("message_ts"), newest.get("message_ts"),
              anchor_external_id, int(overlap_count), complete, int(gap_suspected), now if last_complete_scan else None))
        conn.execute("UPDATE whatsapp_conversations SET sync_status=?, last_synced_at=?, updated_at=? WHERE id=?",
                     (status, now, now, conversation["id"]))
    return sync_state_for_contact(agent_id, contact_name) or {}


def search_messages(agent_id: str, query: str, *, contact_name: str | None = None,
                    limit: int = 20) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    query = str(query or "").strip()
    if not query:
        return []
    sql = """SELECT m.*, c.title AS contact_name FROM whatsapp_messages_fts f
             JOIN whatsapp_messages m ON m.id=f.message_id
             JOIN whatsapp_conversations c ON c.id=m.conversation_id
             WHERE c.agent_id=? AND m.quarantined=0 AND whatsapp_messages_fts MATCH ?"""
    tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    if not tokens:
        return []
    literal_query = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    params: list[Any] = [agent_id, literal_query]
    if contact_name:
        sql += " AND c.title=?"
        params.append(contact_name)
    sql += " ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END, m.message_ts DESC, m.created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 100)))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def runtime_settings(agent_id: str) -> dict[str, Any]:
    init_whatsapp_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_runtime_settings WHERE agent_id=?", (agent_id,)
        ).fetchone()
    if row:
        return dict(row)
    return {"agent_id": agent_id, "automation_enabled": 1, "updated_at": None}


def set_automation_enabled(agent_id: str, enabled: bool) -> dict[str, Any]:
    init_whatsapp_schema()
    now = _now()
    with connect() as conn:
        conn.execute("""
            INSERT INTO whatsapp_runtime_settings(agent_id,automation_enabled,updated_at) VALUES(?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
              automation_enabled=excluded.automation_enabled, updated_at=excluded.updated_at
        """, (agent_id, int(enabled), now))
    emit_event("whatsapp.automation.toggled", agent_id=agent_id, enabled=bool(enabled))
    return runtime_settings(agent_id)


_INTERNAL_OUTBOUND_MARKERS = re.compile(
    r"<\s*(?:CPA_DONE|ACTIS_DONE|TOOL_DONE|DONE)\s*>", re.I
)


def sanitize_outbound_text(content: str) -> str:
    text = _INTERNAL_OUTBOUND_MARKERS.sub("", str(content or ""))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def queue_message(agent_id: str, contact_name: str, content: str, *, source: str = "user",
                  idempotency_key: str | None = None) -> dict[str, Any]:
    content = sanitize_outbound_text(content)
    if not content.strip() or len(content) > 20000:
        raise ValueError("Mensagem deve ter entre 1 e 20000 caracteres não vazios")
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact["name"])
    now, key, outbox_id = _now(), str(idempotency_key or "").strip() or None, uuid.uuid4().hex
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM whatsapp_outbox WHERE agent_id=? AND idempotency_key=?",
            (agent_id, key),
        ).fetchone() if key else None
        if existing:
            if existing["conversation_id"] != conversation["id"] or existing["content"] != content:
                raise ValueError("Chave de idempotência já usada para outra mensagem ou destinatário")
            outbox_id = existing["id"]
        else:
            conn.execute("""INSERT INTO whatsapp_outbox
                (id,agent_id,conversation_id,content,status,attempts,created_at,updated_at,origin_source,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (outbox_id,agent_id,conversation["id"],content,"pending",0,now,now,str(source or "user"),key))
    if not existing:
        emit_event("whatsapp.outbox.queued", agent_id=agent_id,
                   conversation_id=conversation["id"], outbox_id=outbox_id, contact=contact_name)
    return outbox_item(outbox_id) or {}


def queue_document(agent_id: str, contact_name: str, path: str, *, filename: str = "",
                   caption: str = "", source: str = "user",
                   idempotency_key: str | None = None) -> dict[str, Any]:
    """Queue a locally generated PDF document without claiming external delivery."""
    from pathlib import Path
    from meuharness import storage

    file_path = Path(str(path or "")).expanduser().resolve()
    root = storage.DATA_DIR.expanduser().resolve()
    try:
        file_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Documento precisa estar dentro do DATA_DIR do ACTIS") from exc
    if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
        raise ValueError("Documento PDF não encontrado")
    size = file_path.stat().st_size
    if size < 100 or size > 50 * 1024 * 1024:
        raise ValueError("PDF vazio ou acima do limite de 50 MB")
    safe_name = Path(filename or file_path.name).name
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    payload = {"path": str(file_path), "filename": safe_name, "caption": str(caption or "").strip(),
               "sha256": digest, "size": size}
    content = payload["caption"] or safe_name
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact["name"])
    now, key, outbox_id = _now(), str(idempotency_key or "").strip() or None, uuid.uuid4().hex
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM whatsapp_outbox WHERE agent_id=? AND idempotency_key=?", (agent_id, key)
        ).fetchone() if key else None
        if existing:
            same_payload = json.loads(existing["payload"] or "{}") == payload
            if existing["conversation_id"] != conversation["id"] or existing["message_type"] != "document" or not same_payload:
                raise ValueError("Chave de idempotência já usada para outro documento ou destinatário")
            outbox_id = existing["id"]
        else:
            conn.execute("""INSERT INTO whatsapp_outbox
                (id,agent_id,conversation_id,content,message_type,payload,status,attempts,created_at,updated_at,origin_source,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (outbox_id,agent_id,conversation["id"],content,"document",_json(payload),"pending",0,now,now,str(source or "user"),key))
    if not existing:
        emit_event("whatsapp.outbox.queued", agent_id=agent_id, conversation_id=conversation["id"],
                   outbox_id=outbox_id, contact=contact_name, message_type="document")
    return outbox_item(outbox_id) or {}


def outbox_item(outbox_id: str) -> dict[str, Any] | None:
    init_whatsapp_schema()
    with connect() as conn:
        row = conn.execute("""
            SELECT o.*, c.title AS contact_name, c.automation_mode AS conversation_automation_mode, c.status AS conversation_status, ct.external_id AS contact_external_id
            FROM whatsapp_outbox o
            JOIN whatsapp_conversations c ON c.id=o.conversation_id
            JOIN whatsapp_contacts ct ON ct.id=c.contact_id
            WHERE o.id=?
        """, (outbox_id,)).fetchone()
    return dict(row) if row else None


def pending_outbox(agent_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    with connect() as conn:
        rows = conn.execute("""
            SELECT o.*, c.title AS contact_name, c.automation_mode AS conversation_automation_mode, c.status AS conversation_status, ct.external_id AS contact_external_id
            FROM whatsapp_outbox o
            JOIN whatsapp_conversations c ON c.id=o.conversation_id
            JOIN whatsapp_contacts ct ON ct.id=c.contact_id
            WHERE o.agent_id=? AND o.status IN ('pending','failed','sending','blocked','uncertain')
            ORDER BY o.created_at LIMIT ?
        """, (agent_id, max(1, min(int(limit), 100)))).fetchall()
    return [dict(row) for row in rows]


def claim_outbox(agent_id: str, *, limit: int = 1, max_attempts: int = 3) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    now = _now()
    claimed: list[str] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""
            SELECT id FROM whatsapp_outbox
            WHERE agent_id=? AND status IN ('pending','failed') AND attempts < ?
            ORDER BY created_at LIMIT ?
        """, (agent_id, max_attempts, max(1, min(int(limit), 20)))).fetchall()
        for row in rows:
            outbox_id = str(row["id"])
            cursor = conn.execute("""
                UPDATE whatsapp_outbox
                SET status='sending', attempts=attempts+1, last_attempt_at=?, updated_at=?, error=NULL
                WHERE id=? AND status IN ('pending','failed') AND attempts < ?
            """, (now, now, outbox_id, max_attempts))
            if cursor.rowcount:
                claimed.append(outbox_id)
    return [item for outbox_id in claimed if (item := outbox_item(outbox_id))]


def claim_outbox_id(outbox_id: str, *, max_attempts: int = 3) -> dict[str, Any] | None:
    init_whatsapp_schema()
    now = _now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("""
            UPDATE whatsapp_outbox
            SET status='sending', attempts=attempts+1, last_attempt_at=?, updated_at=?, error=NULL
            WHERE id=? AND status IN ('pending','failed') AND attempts < ?
        """, (now, now, outbox_id, max_attempts))
        claimed = bool(cursor.rowcount)
    item = outbox_item(outbox_id)
    return {**item, "_claimed": claimed} if item else None


def complete_outbox(outbox_id: str, *, transport_ref: str | None = None) -> dict[str, Any] | None:
    now = _now()
    with connect() as conn:
        conn.execute("""
            UPDATE whatsapp_outbox
            SET status='sent', sent_at=?, verified_at=?, updated_at=?, error=NULL,
                transport_ref=COALESCE(?,transport_ref)
            WHERE id=?
        """, (now, now, now, transport_ref, outbox_id))
    item = outbox_item(outbox_id)
    if item:
        emit_event(
            "whatsapp.message.sent",
            agent_id=item["agent_id"],
            conversation_id=item["conversation_id"],
            outbox_id=outbox_id,
            contact=item["contact_name"],
            attempts=item["attempts"],
        )
    return item


def block_outbox(outbox_id: str, reason: str) -> dict[str, Any] | None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE whatsapp_outbox SET status='blocked', error=?, updated_at=? WHERE id=?",
            (str(reason)[:1000], now, outbox_id),
        )
    item = outbox_item(outbox_id)
    if item:
        emit_event(
            "whatsapp.outbox.blocked",
            agent_id=item["agent_id"],
            conversation_id=item["conversation_id"],
            outbox_id=outbox_id,
            contact=item["contact_name"],
            reason=str(reason)[:300],
        )
    return item



def release_outbox_for_fallback(outbox_id: str, reason: str) -> dict[str, Any] | None:
    """Return a claimed item to pending only when a transport proves no send was attempted."""
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE whatsapp_outbox
            SET status='pending', attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,
                error=?, updated_at=?
            WHERE id=? AND status='sending'""",
            (str(reason)[:1000], now, outbox_id),
        )
    item = outbox_item(outbox_id)
    if item:
        emit_event(
            "whatsapp.outbox.transport_fallback", agent_id=item["agent_id"],
            conversation_id=item["conversation_id"], outbox_id=outbox_id,
            contact=item["contact_name"], reason=str(reason)[:300],
        )
    return item


def uncertain_outbox(outbox_id: str, error: str) -> dict[str, Any] | None:
    """Mark a delivery as possibly sent. Never auto-retry an ambiguous post-send outcome."""
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE whatsapp_outbox SET status='uncertain', error=?, updated_at=? WHERE id=?",
            (str(error)[:1000], now, outbox_id),
        )
    item = outbox_item(outbox_id)
    if item:
        emit_event(
            "whatsapp.outbox.uncertain", agent_id=item["agent_id"],
            conversation_id=item["conversation_id"], outbox_id=outbox_id,
            contact=item["contact_name"], attempts=item["attempts"], error=str(error)[:300],
        )
    return item

def fail_outbox(outbox_id: str, error: str, *, max_attempts: int = 3) -> dict[str, Any] | None:
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT attempts FROM whatsapp_outbox WHERE id=?", (outbox_id,)).fetchone()
        attempts = int(row["attempts"] or 0) if row else 0
        status = "dead" if attempts >= max_attempts else "failed"
        conn.execute(
            "UPDATE whatsapp_outbox SET status=?, error=?, updated_at=? WHERE id=?",
            (status, str(error)[:1000], now, outbox_id),
        )
    item = outbox_item(outbox_id)
    if item:
        emit_event(
            "whatsapp.outbox.failed",
            agent_id=item["agent_id"],
            conversation_id=item["conversation_id"],
            outbox_id=outbox_id,
            contact=item["contact_name"],
            status=item["status"],
            attempts=item["attempts"],
            error=str(error)[:300],
        )
    return item


def get_conversation_by_contact(agent_id: str, contact_name: str) -> dict[str, Any] | None:
    init_whatsapp_schema()
    with connect() as conn:
        row = conn.execute("""
            SELECT c.* FROM whatsapp_conversations c
            JOIN whatsapp_contacts ct ON ct.id=c.contact_id
            WHERE c.agent_id=? AND ct.name=?
        """, (agent_id, contact_name)).fetchone()
    return dict(row) if row else None


def list_inbox(agent_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    params: list[Any] = [agent_id]
    where = "c.agent_id=?"
    if status:
        where += " AND c.status=?"
        params.append(status)
    params.append(max(1, min(int(limit), 500)))
    with connect() as conn:
        rows = conn.execute(f"""
            SELECT c.*, ct.name AS contact_name, ct.external_id AS contact_external_id,
                   (SELECT m.content FROM whatsapp_messages m WHERE m.conversation_id=c.id
                    ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END,
                             m.message_ts DESC, m.created_at DESC LIMIT 1) AS last_message_content,
                   (SELECT m.direction FROM whatsapp_messages m WHERE m.conversation_id=c.id
                    ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END,
                             m.message_ts DESC, m.created_at DESC LIMIT 1) AS last_message_direction,
                   (SELECT COALESCE(m.message_ts,m.created_at) FROM whatsapp_messages m WHERE m.conversation_id=c.id
                    ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END,
                             m.message_ts DESC, m.created_at DESC LIMIT 1) AS stored_last_message_at,
                   (SELECT COUNT(*) FROM whatsapp_messages m WHERE m.conversation_id=c.id) AS message_count,
                   GROUP_CONCAT(l.name, ',') AS labels
            FROM whatsapp_conversations c
            JOIN whatsapp_contacts ct ON ct.id=c.contact_id
            LEFT JOIN whatsapp_labels l ON l.conversation_id=c.id
            WHERE {where}
            GROUP BY c.id
            ORDER BY COALESCE(stored_last_message_at,c.last_message_at,c.updated_at) DESC
            LIMIT ?
        """, params).fetchall()
    return [dict(row) for row in rows]


VALID_CONVERSATION_STATUS = {"active", "pending", "resolved", "archived"}
VALID_AUTOMATION_MODE = {"autonomous", "supervised", "human_only", "disabled"}
VALID_PRIORITY = {"low", "normal", "high", "urgent"}


def update_conversation(agent_id: str, contact_name: str, *, status: str | None = None,
                        automation_mode: str | None = None, priority: str | None = None,
                        assigned_agent: str | None = None) -> dict[str, Any]:
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    changes: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status not in VALID_CONVERSATION_STATUS:
            raise ValueError("status deve ser active, pending, resolved ou archived")
        changes += ["status=?", "resolved_at=?"]
        params += [status, _now() if status == "resolved" else None]
    if automation_mode is not None:
        if automation_mode not in VALID_AUTOMATION_MODE:
            raise ValueError("automation_mode inválido")
        changes.append("automation_mode=?")
        params.append(automation_mode)
    if priority is not None:
        if priority not in VALID_PRIORITY:
            raise ValueError("priority deve ser low, normal, high ou urgent")
        changes.append("priority=?")
        params.append(priority)
    if assigned_agent is not None:
        changes.append("assigned_agent=?")
        params.append(assigned_agent or None)
    if changes:
        changes.append("updated_at=?")
        params.append(_now())
        params.append(conversation["id"])
        with connect() as conn:
            conn.execute(f"UPDATE whatsapp_conversations SET {', '.join(changes)} WHERE id=?", params)
    row = get_conversation_by_contact(agent_id, contact_name) or conversation
    emit_event(
        "whatsapp.conversation.updated",
        agent_id=agent_id,
        conversation_id=conversation["id"],
        contact=contact_name,
        status=row.get("status"),
        automation_mode=row.get("automation_mode"),
        priority=row.get("priority"),
    )
    return row


def handoff_to_human(agent_id: str, contact_name: str, *, reason: str = "") -> dict[str, Any]:
    row = update_conversation(
        agent_id, contact_name, status="pending", automation_mode="human_only"
    )
    add_label(agent_id, contact_name, "team:human")
    emit_event(
        "whatsapp.handoff", agent_id=agent_id, conversation_id=row["id"],
        contact=contact_name, reason=str(reason)[:300],
    )
    return get_conversation_by_contact(agent_id, contact_name) or row


def resume_automation(agent_id: str, contact_name: str, *, mode: str = "supervised") -> dict[str, Any]:
    if mode not in {"autonomous", "supervised"}:
        raise ValueError("mode deve ser autonomous ou supervised")
    row = update_conversation(agent_id, contact_name, automation_mode=mode)
    remove_label(agent_id, contact_name, "team:human")
    emit_event(
        "whatsapp.handoff.released", agent_id=agent_id, conversation_id=row["id"],
        contact=contact_name, automation_mode=mode,
    )
    return get_conversation_by_contact(agent_id, contact_name) or row


def set_intent(agent_id: str, contact_name: str, intent: str, *, confidence: float | None = None,
               language: str | None = None) -> dict[str, Any]:
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    with connect() as conn:
        conn.execute("""
            UPDATE whatsapp_conversations
            SET intent=?, intent_confidence=?, language=COALESCE(?,language), updated_at=?
            WHERE id=?
        """, (str(intent).strip().lower() or None, confidence, language, _now(), conversation["id"]))
    return get_conversation_by_contact(agent_id, contact_name) or conversation


def _normalize_label(name: str) -> str:
    value = str(name or "").strip().lower().replace(" ", "-")
    if ":" not in value or value.startswith(":") or value.endswith(":"):
        raise ValueError("label deve usar namespace:value, por exemplo intent:orcamento")
    return value


def add_label(agent_id: str, contact_name: str, name: str) -> dict[str, Any]:
    label = _normalize_label(name)
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO whatsapp_labels(conversation_id,name,created_at) VALUES(?,?,?)",
            (conversation["id"], label, _now()),
        )
    return {"conversation_id": conversation["id"], "contact": contact_name, "label": label, "added": True}


def remove_label(agent_id: str, contact_name: str, name: str) -> dict[str, Any]:
    label = _normalize_label(name)
    conversation = get_conversation_by_contact(agent_id, contact_name)
    if not conversation:
        return {"contact": contact_name, "label": label, "removed": False}
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM whatsapp_labels WHERE conversation_id=? AND name=?",
            (conversation["id"], label),
        )
    return {"conversation_id": conversation["id"], "contact": contact_name, "label": label, "removed": bool(cursor.rowcount)}


def list_labels(agent_id: str, *, contact_name: str | None = None) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    params: list[Any] = [agent_id]
    extra = ""
    if contact_name:
        extra = " AND c.title=?"
        params.append(contact_name)
    with connect() as conn:
        rows = conn.execute("""
            SELECT l.name, c.id AS conversation_id, c.title AS contact_name, l.created_at
            FROM whatsapp_labels l
            JOIN whatsapp_conversations c ON c.id=l.conversation_id
            WHERE c.agent_id=?""" + extra + " ORDER BY c.title,l.name", params).fetchall()
    return [dict(row) for row in rows]

def recent_messages(agent_id: str, *, contact_name: str | None = None,
                    limit: int = 40) -> list[dict[str, Any]]:
    init_whatsapp_schema()
    sql = """SELECT m.*, c.title AS contact_name FROM whatsapp_messages m
             JOIN whatsapp_conversations c ON c.id=m.conversation_id
             WHERE c.agent_id=? AND m.quarantined=0"""
    params: list[Any] = [agent_id]
    if contact_name:
        sql += " AND c.title=?"
        params.append(contact_name)
    sql += " ORDER BY CASE WHEN m.message_ts IS NULL OR m.message_ts='' THEN 1 ELSE 0 END, m.message_ts DESC, m.created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def shadow_context(agent_id: str, *, limit: int = 24, contact_name: str | None = None) -> dict[str, Any]:
    """Small local-first context package safe to place in an agent prompt."""
    contacts = list_monitored_contacts(agent_id)
    messages = recent_messages(agent_id, contact_name=contact_name, limit=limit)
    with connect() as conn:
        sync_rows = conn.execute("""SELECT c.title, s.status, s.last_success_at, s.last_message_ts
                                  FROM whatsapp_sync_state s
                                  JOIN whatsapp_conversations c ON c.id=s.conversation_id
                                  WHERE c.agent_id=? ORDER BY s.last_success_at DESC""",
                                 (agent_id,)).fetchall()
    inbox = list_inbox(agent_id, limit=500 if contact_name else 40)
    if contact_name:
        contacts = [row for row in contacts if row["name"] == contact_name]
        inbox = [row for row in inbox if row["contact_name"] == contact_name]
        sync_rows = [row for row in sync_rows if row["title"] == contact_name]
    outbox = pending_outbox(agent_id, limit=100 if contact_name else 10)
    if contact_name:
        outbox = [row for row in outbox if row["contact_name"] == contact_name]
    return {
        "monitored_contacts": [row["name"] for row in contacts],
        "inbox": [
            {
                "contact": row["contact_name"], "status": row["status"],
                "priority": row["priority"], "automation_mode": row["automation_mode"],
                "intent": row["intent"], "assigned_agent": row["assigned_agent"],
                "summary": str(row.get("summary") or "")[:600],
                "labels": [x for x in str(row.get("labels") or "").split(",") if x],
                "last_customer_at": row["last_customer_at"], "last_agent_at": row["last_agent_at"],
            }
            for row in inbox
        ],
        "recent_messages": [
            {"contact": row["contact_name"], "direction": row["direction"],
             "sender": row["sender"], "content": row["content"],
             "timestamp": row["message_ts"]}
            for row in reversed(messages)
        ],
        "sync": [dict(row) for row in sync_rows],
        "outbox": outbox,
    }


def customer_context(agent_id: str, contact_name: str) -> dict[str, Any] | None:
    """Read a customer dossier without conflating it with the agent's own memory."""
    conversation = get_conversation_by_contact(agent_id, contact_name)
    if not conversation:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM whatsapp_conversation_state WHERE conversation_id=?",
                           (conversation["id"],)).fetchone()
    state = dict(row) if row else {}
    payload = json.loads(state.get("payload") or "{}")
    return {"contact": contact_name, "conversation_id": conversation["id"],
            "summary": conversation["summary"], "current_subject": state.get("current_subject", ""),
            "pending_items": json.loads(state.get("pending_items") or "[]"),
            "facts": payload.get("facts", {}), "evidence_ids": payload.get("evidence_ids", []),
            "updated_at": state.get("updated_at"), "sync": sync_state_for_contact(agent_id, contact_name)}


def save_customer_context(agent_id: str, contact_name: str, *, summary: str,
                          current_subject: str = "", pending_items: list[str] | None = None,
                          facts: dict[str, Any] | None = None,
                          evidence_ids: list[str] | None = None) -> dict[str, Any]:
    """Persist an evidenced, inspectable summary using the existing state table."""
    pending_items, facts, evidence_ids = pending_items or [], facts or {}, evidence_ids or []
    if not isinstance(pending_items, list) or not all(isinstance(x, str) for x in pending_items):
        raise ValueError("Pendências devem ser uma lista de textos")
    if not isinstance(facts, dict) or not all(isinstance(k, str) for k in facts):
        raise ValueError("Fatos devem ser um objeto JSON com chaves de texto")
    try:
        _json(facts)
    except (TypeError, ValueError) as exc:
        raise ValueError("Fatos devem conter apenas valores JSON válidos") from exc
    if not isinstance(evidence_ids, list) or not all(isinstance(x, str) for x in evidence_ids):
        raise ValueError("IDs de evidência devem ser uma lista de textos")
    if len(summary) > 6000 or len(_json([pending_items, facts, evidence_ids])) > 20000:
        raise ValueError("Contexto muito extenso")
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    now = _now()
    with connect() as conn:
        for evidence in evidence_ids:
            if not conn.execute("SELECT 1 FROM whatsapp_messages WHERE conversation_id=? AND quarantined=0 AND (id=? OR external_id=?)",
                                (conversation["id"], evidence, evidence)).fetchone():
                raise ValueError("ID de evidência não pertence ao histórico deste contato")
        conn.execute("UPDATE whatsapp_conversations SET summary=?,updated_at=? WHERE id=?",
                     (str(summary).strip(), now, conversation["id"]))
        conn.execute("""INSERT INTO whatsapp_conversation_state
            (conversation_id,current_subject,pending_items,payload,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET current_subject=excluded.current_subject,
            pending_items=excluded.pending_items,payload=excluded.payload,updated_at=excluded.updated_at""",
            (conversation["id"],str(current_subject)[:1000],_json(pending_items),
             _json({"facts":facts,"evidence_ids":evidence_ids}),now))
    emit_event("whatsapp.context.updated",agent_id=agent_id,conversation_id=conversation["id"],
               contact=contact_name,evidence_count=len(evidence_ids))
    return customer_context(agent_id, contact_name) or {}


def quarantine_ambiguous_messages(agent_id: str) -> dict[str, Any]:
    """Reversible repair: exclude IDs previously attributed to multiple conversations."""
    init_whatsapp_schema()
    with connect() as conn:
        ids = [r[0] for r in conn.execute("""SELECT m.external_id FROM whatsapp_messages m
            JOIN whatsapp_conversations c ON c.id=m.conversation_id
            WHERE c.agent_id=? AND m.external_id IS NOT NULL AND m.external_id<>''
            GROUP BY m.external_id HAVING COUNT(DISTINCT m.conversation_id)>1""", (agent_id,))]
        affected = set()
        for ident in ids:
            for row in conn.execute("""SELECT m.conversation_id FROM whatsapp_messages m
                JOIN whatsapp_conversations c ON c.id=m.conversation_id
                WHERE c.agent_id=? AND m.external_id=?""", (agent_id,ident)):
                affected.add(row[0])
            conn.execute("""UPDATE whatsapp_messages SET quarantined=1 WHERE external_id=? AND conversation_id IN
                (SELECT id FROM whatsapp_conversations WHERE agent_id=?)""",(ident,agent_id))
        for conversation_id in affected:
            conn.execute("UPDATE whatsapp_conversations SET sync_status='needs_verification',summary='' WHERE id=?",(conversation_id,))
            conn.execute("""UPDATE whatsapp_sync_state SET status='needs_verification',gap_suspected=1,
                error='legacy_cross_contact_message_ids',backfill_complete=0 WHERE conversation_id=?""",(conversation_id,))
    return {"ambiguous_ids":len(ids),"conversations":len(affected),"deleted":0}
