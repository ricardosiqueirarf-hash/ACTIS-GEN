"""Persistent cross-conversation memory for ACTIS GEN agents."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from meuharness.storage import connect, init_db, memory_search_uses_fts5


def _now() -> str:
    return datetime.now(UTC).isoformat()


def remember(agent_id: str, content: str, *, conversation_id: str | None = None) -> str | None:
    content = re.sub(r"\s+", " ", str(content or "")).strip()
    if not content:
        return None
    memory_id = uuid4().hex
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO memories(id,agent_id,conversation_id,content,created_at) VALUES(?,?,?,?,?)",
            (memory_id, agent_id, conversation_id, content[:4000], _now()),
        )
        conn.execute(
            "INSERT INTO memories_fts(id,agent_id,content) VALUES(?,?,?)",
            (memory_id, agent_id, content[:4000]),
        )
    return memory_id


def _query_terms(text: str) -> list[str]:
    words = re.findall(r"[\wÀ-ÿ-]{3,}", text.lower(), flags=re.UNICODE)
    stop = {"para", "com", "uma", "que", "isso", "esta", "esse", "como", "mais", "por", "dos", "das"}
    result: list[str] = []
    for word in words:
        if word not in stop and word not in result:
            result.append(word)
    return result[:8]


def _recall_like(conn, agent_id: str, terms: list[str], max_items: int):
    if not terms:
        return []
    clauses = " OR ".join("LOWER(content) LIKE ?" for _ in terms)
    params = [agent_id, *[f"%{term.lower()}%" for term in terms], max_items]
    return list(conn.execute(
        f"""SELECT id,content,created_at,conversation_id FROM memories
            WHERE agent_id=? AND ({clauses})
            ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall())


def recall(agent_id: str, prompt: str, *, limit: int = 6) -> list[dict[str, str]]:
    init_db()
    terms = _query_terms(prompt)
    max_items = max(1, min(limit, 20))
    fts5 = memory_search_uses_fts5()
    with connect() as conn:
        rows = []
        if terms and fts5:
            query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
            rows = list(conn.execute(
                """SELECT m.id,m.content,m.created_at,m.conversation_id
                   FROM memories_fts f JOIN memories m ON m.id=f.id
                   WHERE f.agent_id=? AND memories_fts MATCH ?
                   ORDER BY bm25(memories_fts), m.created_at DESC LIMIT ?""",
                (agent_id, query, max_items),
            ).fetchall())
        elif terms:
            rows = _recall_like(conn, agent_id, terms, max_items)
        seen = {str(row["id"]) for row in rows}
        if len(rows) < max_items:
            recent = conn.execute(
                """SELECT id,content,created_at,conversation_id FROM memories
                   WHERE agent_id=? ORDER BY created_at DESC LIMIT ?""",
                (agent_id, max_items * 3),
            ).fetchall()
            rows.extend(row for row in recent if str(row["id"]) not in seen)
    return [dict(row) for row in rows[:max_items]]
