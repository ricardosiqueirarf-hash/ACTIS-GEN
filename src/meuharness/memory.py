"""Persistent cross-conversation memory for ACTIS GEN agents."""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from meuharness.storage import connect, init_db, memory_fts_available


def _now() -> str:
    return datetime.now(UTC).isoformat()


def remember(agent_id: str, content: str, *, conversation_id: str | None = None) -> str | None:
    content = re.sub(r"\s+", " ", str(content or "")).strip()
    if not content:
        return None
    memory_id = uuid4().hex
    fts_enabled = memory_fts_available()
    with connect() as conn:
        conn.execute(
            "INSERT INTO memories(id,agent_id,conversation_id,content,created_at) VALUES(?,?,?,?,?)",
            (memory_id, agent_id, conversation_id, content[:4000], _now()),
        )
        if fts_enabled:
            try:
                conn.execute(
                    "INSERT INTO memories_fts(id,agent_id,content) VALUES(?,?,?)",
                    (memory_id, agent_id, content[:4000]),
                )
            except sqlite3.OperationalError:
                # The base memory row is still valid. Android builds may expose
                # SQLite without FTS5, so search can safely fall back to recency.
                pass
    return memory_id


def _query_terms(text: str) -> list[str]:
    words = re.findall(r"[\wÀ-ÿ-]{3,}", text.lower(), flags=re.UNICODE)
    stop = {"para", "com", "uma", "que", "isso", "esta", "esse", "como", "mais", "por", "dos", "das"}
    result: list[str] = []
    for word in words:
        if word not in stop and word not in result:
            result.append(word)
    return result[:8]


def recall(agent_id: str, prompt: str, *, limit: int = 6) -> list[dict[str, str]]:
    init_db()
    terms = _query_terms(prompt)
    max_items = max(1, min(limit, 20))
    fts_enabled = memory_fts_available()
    with connect() as conn:
        rows = []
        if terms and fts_enabled:
            query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
            try:
                rows = list(conn.execute(
                    """SELECT m.id,m.content,m.created_at,m.conversation_id
                       FROM memories_fts f JOIN memories m ON m.id=f.id
                       WHERE f.agent_id=? AND memories_fts MATCH ?
                       ORDER BY bm25(memories_fts), m.created_at DESC LIMIT ?""",
                    (agent_id, query, max_items),
                ).fetchall())
            except sqlite3.OperationalError:
                rows = []
        seen = {str(row["id"]) for row in rows}
        if len(rows) < max_items:
            recent = conn.execute(
                """SELECT id,content,created_at,conversation_id FROM memories
                   WHERE agent_id=? ORDER BY created_at DESC LIMIT ?""",
                (agent_id, max_items * 3),
            ).fetchall()
            rows.extend(row for row in recent if str(row["id"]) not in seen)
    return [dict(row) for row in rows[:max_items]]
