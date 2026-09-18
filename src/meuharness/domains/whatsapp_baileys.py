"""ACTIS client for the local Baileys WhatsApp transport.

Baileys owns only the linked WhatsApp socket. ACTIS remains authoritative for
contacts, message shadow state, routing, outbox, idempotency and completion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from meuharness import storage
from meuharness.domains.whatsapp_transport import normalize_phone, phone_aliases
from meuharness.storage import connect

_DEFAULT_URL = "http://127.0.0.1:8789"


def _base_url() -> str:
    return str(os.environ.get("ACTIS_BAILEYS_URL") or _DEFAULT_URL).rstrip("/")


def _token_file() -> Path:
    configured = str(os.environ.get("ACTIS_BAILEYS_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return storage.DATA_DIR / "whatsapp-baileys" / "token"


def _token() -> str:
    path = _token_file()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 4.0,
) -> tuple[int, dict[str, Any]]:
    token = _token()
    if not token:
        return 0, {"ok": False, "status": "unavailable", "reason": "token_missing"}
    data = None
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(_base_url() + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw else {}
            return int(response.status), body if isinstance(body, dict) else {}
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
            body = {"ok": False, "error": str(exc)}
        return int(exc.code), body if isinstance(body, dict) else {}
    except (URLError, TimeoutError, OSError) as exc:
        return 0, {
            "ok": False,
            "status": "unavailable",
            "reason": "bridge_unreachable",
            "error": str(exc)[:300],
        }


def health() -> dict[str, Any]:
    status, body = _request("GET", "/health", timeout=2.0)
    if status != 200:
        return {
            **body,
            "ok": False,
            "connected": False,
            "status": str(body.get("status") or "unavailable"),
        }
    return body


def primary_connected() -> bool:
    state = health()
    return bool(state.get("connected") and state.get("status") == "open")


def init_baileys_state() -> None:
    with connect() as conn:
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS whatsapp_baileys_state (
                agent_id TEXT PRIMARY KEY,
                last_event_seq INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS whatsapp_baileys_peers (
                agent_id TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                remote_jid TEXT NOT NULL,
                phone TEXT,
                peer_kind TEXT NOT NULL DEFAULT 'direct',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(agent_id, contact_name)
            );"""
        )


def _cursor(agent_id: str) -> int:
    init_baileys_state()
    with connect() as conn:
        row = conn.execute(
            "SELECT last_event_seq FROM whatsapp_baileys_state WHERE agent_id=?",
            (agent_id,),
        ).fetchone()
    return int(row["last_event_seq"] or 0) if row else 0


def _set_cursor(agent_id: str, seq: int) -> None:
    init_baileys_state()
    with connect() as conn:
        conn.execute(
            """INSERT INTO whatsapp_baileys_state(agent_id,last_event_seq,updated_at)
            VALUES(?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(agent_id) DO UPDATE SET
              last_event_seq=excluded.last_event_seq,
              updated_at=CURRENT_TIMESTAMP""",
            (agent_id, int(seq)),
        )


def _remember_peer(
    agent_id: str, contact_name: str, remote_jid: str, phone: str, peer_kind: str
) -> None:
    init_baileys_state()
    if not remote_jid:
        return
    with connect() as conn:
        conn.execute(
            """INSERT INTO whatsapp_baileys_peers
            (agent_id,contact_name,remote_jid,phone,peer_kind,updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(agent_id,contact_name) DO UPDATE SET
              remote_jid=excluded.remote_jid,
              phone=COALESCE(excluded.phone,whatsapp_baileys_peers.phone),
              peer_kind=excluded.peer_kind,
              updated_at=CURRENT_TIMESTAMP""",
            (agent_id, contact_name, remote_jid, phone or None, peer_kind),
        )


def peer_for_contact(agent_id: str, contact_name: str) -> dict[str, Any] | None:
    init_baileys_state()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_baileys_peers WHERE agent_id=? AND contact_name=?",
            (agent_id, contact_name),
        ).fetchone()
    return dict(row) if row else None


def _existing_contact_name(agent_id: str, phone: str, remote_jid: str) -> str:
    """Resolve one WhatsApp peer to the canonical ACTIS contact.

    Phone identity wins over LID/JID identity. This matters in Brazil because
    WhatsApp can emit the legacy mobile number without the added ninth digit,
    and an outgoing event may arrive with only an opaque @lid before the next
    incoming event. Resolving row-by-row used to let that opaque LID create a
    duplicate supervised conversation, so later customer messages stopped
    reaching the autonomous contact.
    """
    wanted = phone_aliases(phone) if phone else set()
    with connect() as conn:
        rows = conn.execute(
            "SELECT name,external_id FROM whatsapp_contacts WHERE agent_id=?",
            (agent_id,),
        ).fetchall()

        # 1) Prefer a real phone match across the whole contact set.
        if wanted:
            for row in rows:
                values = [str(row["external_id"] or ""), str(row["name"] or "")]
                for value in values:
                    normalized = normalize_phone(value)
                    if normalized and normalized in wanted:
                        return str(row["name"])

        # 2) If this event has only a LID/JID, reuse an already observed peer
        # whose stored phone resolves back to a canonical contact.
        if remote_jid:
            peers = conn.execute(
                """SELECT contact_name,phone FROM whatsapp_baileys_peers
                WHERE agent_id=? AND remote_jid=? ORDER BY updated_at""",
                (agent_id, remote_jid),
            ).fetchall()
            by_name = {str(row["name"]): row for row in rows}
            for peer in peers:
                peer_phone = str(peer["phone"] or "")
                aliases = phone_aliases(peer_phone) if peer_phone else set()
                contact = by_name.get(str(peer["contact_name"] or ""))
                if not contact or not aliases:
                    continue
                values = [str(contact["external_id"] or ""), str(contact["name"] or "")]
                if any(
                    (normalized := normalize_phone(value)) and normalized in aliases
                    for value in values
                ):
                    return str(contact["name"])

        # 3) Last resort: exact JID/LID stored directly on a contact.
        if remote_jid:
            for row in rows:
                values = [str(row["external_id"] or ""), str(row["name"] or "")]
                if remote_jid in values:
                    return str(row["name"])
    return ""


def _contact_for_event(agent_id: str, event: dict[str, Any]) -> tuple[str, str, str]:
    from meuharness.domains.whatsapp_shadow import upsert_contact

    phone = normalize_phone(str(event.get("phone") or ""))
    remote_jid = str(event.get("remote_jid") or "").strip()
    kind = str(event.get("peer_kind") or "direct").strip() or "direct"
    existing = _existing_contact_name(agent_id, phone, remote_jid)
    if existing:
        _remember_peer(agent_id, existing, remote_jid, phone, kind)
        return existing, phone, kind
    name = phone or remote_jid
    if not name:
        raise ValueError("Evento Baileys sem peer identificável")
    upsert_contact(
        agent_id,
        name,
        external_id=phone or remote_jid,
        kind="group" if kind == "group" else "contact",
        monitored=True,
    )
    _remember_peer(agent_id, name, remote_jid, phone, kind)
    return name, phone, kind


def _events(after: int, limit: int = 200) -> list[dict[str, Any]]:
    query = urlencode({"after": max(0, int(after)), "limit": max(1, min(limit, 500))})
    status, body = _request("GET", "/events?" + query, timeout=3.0)
    if status != 200 or not body.get("ok"):
        raise RuntimeError(str(body.get("error") or body.get("reason") or "events_unavailable"))
    rows = body.get("events")
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def sync_once(agent_id: str, *, limit: int = 200) -> dict[str, Any]:
    """Consume durable Baileys events into the canonical ACTIS WhatsApp shadow."""
    state = health()
    if not state.get("connected"):
        return {
            "ok": False,
            "transport": "baileys",
            "reason": "primary_unavailable",
            "primary_status": state.get("status"),
            "synced": 0,
        }

    from meuharness.domains.whatsapp_shadow import ingest_messages, set_sync_continuity

    cursor = _cursor(agent_id)
    try:
        rows = _events(cursor, limit=limit)
    except (RuntimeError, ValueError, OSError) as exc:
        return {
            "ok": False,
            "transport": "baileys",
            "reason": "event_read_failed",
            "error": str(exc)[:300],
            "synced": 0,
        }

    inserted = 0
    processed = 0
    newest_seq = cursor
    contacts: dict[str, int] = {}
    for row in rows:
        seq = int(row.get("seq") or 0)
        contact_name, _phone, _kind = _contact_for_event(agent_id, row)
        message = {
            "external_id": str(row.get("message_id") or ""),
            "direction": str(row.get("direction") or "incoming"),
            "sender": str(row.get("sender") or ""),
            "content": str(row.get("content") or ""),
            "message_type": str(row.get("message_type") or "text"),
            "timestamp": row.get("message_ts"),
            "transport": "baileys",
            "remote_jid": str(row.get("remote_jid") or ""),
        }
        result = ingest_messages(
            agent_id,
            contact_name,
            [message],
            emit_inbound_events=True,
            verified_contact=True,
        )
        inserted += int(result.get("inserted") or 0)
        processed += 1
        contacts[contact_name] = contacts.get(contact_name, 0) + int(result.get("inserted") or 0)
        set_sync_continuity(
            agent_id,
            contact_name,
            status="healthy",
            overlap_count=1,
            gap_suspected=False,
        )
        newest_seq = max(newest_seq, seq)
        _set_cursor(agent_id, newest_seq)

    return {
        "ok": True,
        "transport": "baileys",
        "primary_status": state.get("status"),
        "synced": inserted,
        "events": processed,
        "cursor": newest_seq,
        "contacts": contacts,
    }


def _recipient_payload(item: dict[str, Any]) -> dict[str, str]:
    agent_id = str(item.get("agent_id") or "")
    contact = str(item.get("contact_name") or "")
    peer = peer_for_contact(agent_id, contact) if agent_id and contact else None
    remote_jid = str((peer or {}).get("remote_jid") or "").strip()
    if remote_jid:
        return {"jid": remote_jid}
    external = str(item.get("contact_external_id") or "")
    phone = normalize_phone(external or contact)
    if phone:
        return {"phone": phone}
    jid = external if "@" in external else (contact if "@" in contact else "")
    if jid:
        return {"jid": jid}
    raise ValueError("Destinatário sem telefone/JID verificável")


def send_outbox(item: dict[str, Any]) -> dict[str, Any]:
    """Attempt one Baileys send and classify whether Web fallback is safe."""
    recipient = _recipient_payload(item)
    message_type = str(item.get("message_type") or "text")
    payload: dict[str, Any] = {
        **recipient,
        "client_id": str(item.get("id") or ""),
    }
    endpoint = "/send"
    if message_type == "document":
        try:
            document = json.loads(str(item.get("payload") or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("Payload de documento inválido") from exc
        payload.update(
            {
                "path": str(document.get("path") or ""),
                "filename": str(document.get("filename") or ""),
                "caption": str(document.get("caption") or ""),
            }
        )
        endpoint = "/send-document"
    else:
        payload["content"] = str(item.get("content") or "")

    status, body = _request("POST", endpoint, payload=payload, timeout=70.0)
    result = dict(body)
    result["http_status"] = status
    if status == 0:
        result.setdefault("status", "unavailable")
        result.setdefault("attempted", False)
        result.setdefault("safe_fallback", True)
    return result


def delivery_status(message_id: str) -> dict[str, Any]:
    query = urlencode({"id": str(message_id or "")})
    status, body = _request("GET", "/delivery?" + query, timeout=2.0)
    return {**body, "http_status": status}
