"""Durable execution journal and recovery helpers for ACTIS GEN Runs."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.storage import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    evidence TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_run_checkpoints_run_seq
ON run_checkpoints(run_id, seq ASC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_checkpoints_idempotency
ON run_checkpoints(run_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
"""

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _init() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def _row_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    try:
        item["payload"] = json.loads(item.get("payload") or "{}")
    except json.JSONDecodeError:
        item["payload"] = {}
    return item


def append_checkpoint(
    run_id: str,
    agent_id: str,
    phase: str,
    *,
    status: str = "completed",
    detail: str | None = None,
    evidence: str | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Append one atomic checkpoint; repeated idempotency keys return the original row."""
    _init()
    clean_key = str(idempotency_key or "").strip() or None
    with connect() as conn:
        if clean_key:
            existing = conn.execute(
                "SELECT * FROM run_checkpoints WHERE run_id=? AND idempotency_key=?",
                (run_id, clean_key),
            ).fetchone()
            if existing:
                return _row_dict(existing)
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM run_checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()
        seq = int(row["seq"])
        checkpoint_id = uuid4().hex
        conn.execute(
            """INSERT INTO run_checkpoints
            (id,run_id,agent_id,seq,phase,status,detail,evidence,payload,idempotency_key,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                checkpoint_id, run_id, agent_id, seq, phase, status,
                detail, evidence,
                json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
                clean_key, _now(),
            ),
        )
        saved = conn.execute(
            "SELECT * FROM run_checkpoints WHERE id=?", (checkpoint_id,)
        ).fetchone()
    return _row_dict(saved)

def list_checkpoints(run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    _init()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY seq ASC LIMIT ?",
            (run_id, max(1, min(int(limit), 1000))),
        ).fetchall()
    return [_row_dict(row) for row in rows]


def latest_checkpoint(run_id: str) -> dict[str, Any] | None:
    _init()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    return _row_dict(row) if row else None


def recovery_summary(run_id: str, *, limit: int = 20) -> str:
    rows = list_checkpoints(run_id, limit=1000)[-max(1, min(limit, 50)):]
    if not rows:
        return "Nenhum checkpoint durável foi registrado."
    lines: list[str] = []
    for row in rows:
        text = f"#{row['seq']} {row['phase']} [{row['status']}]"
        if row.get("detail"):
            text += f" — {row['detail']}"
        if row.get("evidence"):
            text += f" | evidência: {row['evidence']}"
        lines.append(text)
    return "\n".join(lines)
