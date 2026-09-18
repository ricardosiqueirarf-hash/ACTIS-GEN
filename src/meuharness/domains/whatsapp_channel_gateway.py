"""OpenClaw-inspired WhatsApp channel routing for ACTIS shadow agents.

The production WhatsApp agent remains the sole transport owner. This module
fans an inbound envelope out to isolated shadow agents before LLM execution,
stores per-agent/per-peer session history, and never exposes an outbound path.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from meuharness.event_bus import emit_event
from meuharness.storage import connect


def _now() -> str:
    return datetime.now(UTC).isoformat()


def shadow_runtime_enabled() -> bool:
    return str(os.getenv("ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}


def init_channel_gateway_schema() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_channel_bindings (
                id TEXT PRIMARY KEY,
                transport_agent_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                peer_kind TEXT NOT NULL DEFAULT 'direct',
                peer_id TEXT NOT NULL DEFAULT '*',
                mode TEXT NOT NULL DEFAULT 'shadow',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(transport_agent_id, agent_id, peer_kind, peer_id)
            );
            CREATE INDEX IF NOT EXISTS idx_wa_channel_bindings_route
            ON whatsapp_channel_bindings(transport_agent_id, peer_kind, peer_id, enabled);

            CREATE TABLE IF NOT EXISTS whatsapp_channel_jobs (
                id TEXT PRIMARY KEY,
                binding_id TEXT NOT NULL,
                transport_agent_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                peer_kind TEXT NOT NULL,
                peer_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                source_external_id TEXT,
                content TEXT NOT NULL,
                sender TEXT NOT NULL DEFAULT '',
                message_ts TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                run_id TEXT,
                response TEXT,
                latency_ms INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(binding_id, source_message_id),
                FOREIGN KEY(binding_id) REFERENCES whatsapp_channel_bindings(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_wa_channel_jobs_queue
            ON whatsapp_channel_jobs(transport_agent_id, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_wa_channel_jobs_session
            ON whatsapp_channel_jobs(session_key, created_at);
            """
        )


def _binding_id(transport_agent_id: str, agent_id: str, peer_kind: str, peer_id: str) -> str:
    raw = f"{transport_agent_id}|{agent_id}|{peer_kind}|{peer_id}".encode()
    return "wab-" + hashlib.sha256(raw).hexdigest()[:20]


def session_key(agent_id: str, transport_agent_id: str, peer_kind: str, peer_id: str) -> str:
    return f"agent:{agent_id}:whatsapp:account:{transport_agent_id}:{peer_kind}:{peer_id}"


def upsert_binding(
    transport_agent_id: str,
    agent_id: str,
    *,
    peer_kind: str = "direct",
    peer_id: str = "*",
    enabled: bool = True,
) -> dict[str, Any]:
    init_channel_gateway_schema()
    transport_agent_id = str(transport_agent_id or "").strip()
    agent_id = str(agent_id or "").strip()
    peer_kind = str(peer_kind or "direct").strip() or "direct"
    peer_id = str(peer_id or "*").strip() or "*"
    if not transport_agent_id or not agent_id:
        raise ValueError("transport_agent_id e agent_id são obrigatórios")
    ident = _binding_id(transport_agent_id, agent_id, peer_kind, peer_id)
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO whatsapp_channel_bindings
            (id,transport_agent_id,agent_id,peer_kind,peer_id,mode,enabled,created_at,updated_at)
            VALUES(?,?,?,?,?,'shadow',?,?,?)
            ON CONFLICT(transport_agent_id,agent_id,peer_kind,peer_id) DO UPDATE SET
            enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (ident, transport_agent_id, agent_id, peer_kind, peer_id, int(enabled), now, now),
        )
        row = conn.execute("SELECT * FROM whatsapp_channel_bindings WHERE id=?", (ident,)).fetchone()
    return dict(row)


def set_binding_enabled(binding_id: str, enabled: bool) -> dict[str, Any] | None:
    init_channel_gateway_schema()
    with connect() as conn:
        conn.execute(
            "UPDATE whatsapp_channel_bindings SET enabled=?,updated_at=? WHERE id=?",
            (int(enabled), _now(), binding_id),
        )
        row = conn.execute("SELECT * FROM whatsapp_channel_bindings WHERE id=?", (binding_id,)).fetchone()
    return dict(row) if row else None


def list_bindings(transport_agent_id: str = "") -> list[dict[str, Any]]:
    init_channel_gateway_schema()
    with connect() as conn:
        if transport_agent_id:
            rows = conn.execute(
                "SELECT * FROM whatsapp_channel_bindings WHERE transport_agent_id=? ORDER BY created_at",
                (transport_agent_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM whatsapp_channel_bindings ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def route_inbound_message(
    transport_agent_id: str,
    contact_name: str,
    message: dict[str, Any],
    *,
    peer_kind: str = "direct",
) -> list[dict[str, Any]]:
    """Fan out one normalized inbound envelope to configured shadow agents."""
    if not shadow_runtime_enabled():
        return []
    init_channel_gateway_schema()
    source_message_id = str(message.get("message_id") or "").strip()
    if not source_message_id:
        raise ValueError("message_id é obrigatório para roteamento idempotente")
    peer_id = str(contact_name or "").strip()
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM whatsapp_channel_bindings
            WHERE transport_agent_id=? AND peer_kind=? AND enabled=1 AND mode='shadow'
              AND (peer_id=? OR peer_id='*')
            ORDER BY CASE WHEN peer_id=? THEN 0 ELSE 1 END, created_at""",
            (transport_agent_id, peer_kind, peer_id, peer_id),
        ).fetchall()
        routed: list[dict[str, Any]] = []
        seen_agents: set[str] = set()
        now = _now()
        for binding in rows:
            agent_id = str(binding["agent_id"])
            if agent_id in seen_agents:
                continue
            seen_agents.add(agent_id)
            job_id = uuid.uuid4().hex
            key = session_key(agent_id, transport_agent_id, peer_kind, peer_id)
            conn.execute(
                """INSERT OR IGNORE INTO whatsapp_channel_jobs
                (id,binding_id,transport_agent_id,agent_id,peer_kind,peer_id,session_key,
                 source_message_id,source_external_id,content,sender,message_ts,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    binding["id"],
                    transport_agent_id,
                    agent_id,
                    peer_kind,
                    peer_id,
                    key,
                    source_message_id,
                    str(message.get("external_id") or "") or None,
                    str(message.get("content") or ""),
                    str(message.get("sender") or ""),
                    message.get("timestamp"),
                    now,
                ),
            )
            saved = conn.execute(
                "SELECT * FROM whatsapp_channel_jobs WHERE binding_id=? AND source_message_id=?",
                (binding["id"], source_message_id),
            ).fetchone()
            if saved:
                routed.append(dict(saved))
    for job in routed:
        emit_event(
            "whatsapp.channel.shadow.queued",
            agent_id=job["agent_id"],
            transport_agent_id=transport_agent_id,
            contact=peer_id,
            job_id=job["id"],
            session_key=job["session_key"],
        )
    return routed


def _claim_shadow_job(transport_agent_id: str, peer_id: str = "") -> dict[str, Any] | None:
    init_channel_gateway_schema()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        params: list[Any] = [transport_agent_id]
        where = "transport_agent_id=? AND status='queued'"
        if peer_id:
            where += " AND peer_id=?"
            params.append(peer_id)
        row = conn.execute(
            f"SELECT * FROM whatsapp_channel_jobs WHERE {where} ORDER BY created_at LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE whatsapp_channel_jobs SET status='processing',started_at=? WHERE id=? AND status='queued'",
            (_now(), row["id"]),
        )
        claimed = conn.execute("SELECT * FROM whatsapp_channel_jobs WHERE id=?", (row["id"],)).fetchone()
    return dict(claimed) if claimed else None


def _session_history(session: str, *, before_job_id: str, limit: int = 8) -> list[dict[str, str]]:
    init_channel_gateway_schema()
    with connect() as conn:
        current = conn.execute(
            "SELECT created_at FROM whatsapp_channel_jobs WHERE id=?", (before_job_id,)
        ).fetchone()
        if not current:
            return []
        rows = conn.execute(
            """SELECT content,response FROM whatsapp_channel_jobs
            WHERE session_key=? AND status='completed' AND created_at<?
            ORDER BY created_at DESC LIMIT ?""",
            (session, current["created_at"], max(1, min(limit, 20))),
        ).fetchall()
    history: list[dict[str, str]] = []
    for row in reversed(rows):
        history.append({"role": "user", "content": str(row["content"] or "")})
        if row["response"]:
            history.append({"role": "assistant", "content": str(row["response"])})
    return history


def _shadow_agent_is_safe(agent: dict[str, Any] | None) -> bool:
    if not agent:
        return False
    return not (
        agent.get("company_id")
        or list(agent.get("tools") or [])
        or list(agent.get("skills") or [])
        or list(agent.get("permissions") or [])
    )


def process_shadow_once(transport_agent_id: str, *, peer_id: str = "") -> dict[str, Any]:
    """Execute one queued shadow participant. No WhatsApp outbound capability is exposed."""
    if not shadow_runtime_enabled():
        return {"ok": True, "processed": 0, "reason": "shadow_disabled"}
    job = _claim_shadow_job(transport_agent_id, peer_id)
    if not job:
        return {"ok": True, "processed": 0}
    from meuharness.agents import get_agent
    from meuharness.execution_service import execute_agent

    agent = get_agent(str(job["agent_id"]))
    if not _shadow_agent_is_safe(agent):
        error = "shadow_agent_not_isolated"
        with connect() as conn:
            conn.execute(
                "UPDATE whatsapp_channel_jobs SET status='blocked',error=?,finished_at=? WHERE id=?",
                (error, _now(), job["id"]),
            )
        return {"ok": False, "processed": 1, "status": "blocked", "reason": error, "job_id": job["id"]}

    history = _session_history(str(job["session_key"]), before_job_id=str(job["id"]))
    prompt = (
        "SHADOW WHATSAPP BENCHMARK. Você é um segundo agente candidato, não o agente que envia ao cliente. "
        "Produza somente a mensagem curta que você enviaria em resposta à mensagem recebida. "
        "Não use ferramentas, não execute ações, não crie tarefas, não altere estado e não alegue que enviou nada. "
        "Se faltarem dados, faça apenas a pergunta necessária. Mensagem recebida: "
        + repr(str(job["content"] or ""))
    )
    started = time.monotonic()
    try:
        result = asyncio.run(
            execute_agent(
                str(job["agent_id"]),
                prompt,
                history=history,
                source="automation",
                conversation_id=str(job["session_key"]),
                disable_tools=True,
            )
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        response = str(result.text or "").strip()
        with connect() as conn:
            conn.execute(
                """UPDATE whatsapp_channel_jobs SET status='completed',run_id=?,response=?,
                latency_ms=?,finished_at=?,error=NULL WHERE id=?""",
                (result.run_id, response, latency_ms, _now(), job["id"]),
            )
        emit_event(
            "whatsapp.channel.shadow.completed",
            agent_id=job["agent_id"],
            transport_agent_id=transport_agent_id,
            contact=job["peer_id"],
            job_id=job["id"],
            run_id=result.run_id,
            latency_ms=latency_ms,
            candidate=response,
        )
        return {
            "ok": True,
            "processed": 1,
            "status": "completed",
            "job_id": job["id"],
            "run_id": result.run_id,
            "response": response,
            "latency_ms": latency_ms,
        }
    except Exception as exc:  # noqa: BLE001 - persist benchmark failure without affecting production
        latency_ms = int((time.monotonic() - started) * 1000)
        error = str(exc)[:500]
        with connect() as conn:
            conn.execute(
                """UPDATE whatsapp_channel_jobs SET status='failed',latency_ms=?,error=?,
                finished_at=? WHERE id=?""",
                (latency_ms, error, _now(), job["id"]),
            )
        emit_event(
            "whatsapp.channel.shadow.failed",
            agent_id=job["agent_id"],
            transport_agent_id=transport_agent_id,
            contact=job["peer_id"],
            job_id=job["id"],
            error=error,
        )
        return {
            "ok": False,
            "processed": 1,
            "status": "failed",
            "job_id": job["id"],
            "error": error,
            "latency_ms": latency_ms,
        }


def list_shadow_jobs(
    transport_agent_id: str = "",
    *,
    agent_id: str = "",
    peer_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_channel_gateway_schema()
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("transport_agent_id", transport_agent_id),
        ("agent_id", agent_id),
        ("peer_id", peer_id),
    ):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit), 200)))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM whatsapp_channel_jobs{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]
