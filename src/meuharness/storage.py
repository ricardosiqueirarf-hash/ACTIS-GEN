"""SQLite-backed canonical persistence for ACTIS GEN."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".local" / "share" / "actis-gen"
DB_FILE = DATA_DIR / "actis.db"
_LOCK = threading.RLock()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS collections (
            name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(name, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_collections_name_pos
        ON collections(name, position);
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            run_id TEXT,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            conversation_id TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_agent_created
        ON memories(agent_id, created_at DESC);
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            id UNINDEXED, agent_id UNINDEXED, content
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at DESC);
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            status TEXT NOT NULL,
            input TEXT,
            output TEXT,
            error TEXT,
            trace TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_started
        ON workflow_runs(workflow_id, started_at DESC);
        """)
        from meuharness.enterprise import FOUNDATION_SCHEMA

        conn.executescript(FOUNDATION_SCHEMA)


def load_collection(name: str) -> list[dict[str, Any]]:
    init_db()
    with _LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM collections WHERE name=? ORDER BY position ASC",
            (name,),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_collection(name: str, items: list[dict[str, Any]]) -> None:
    init_db()
    with _LOCK, connect() as conn:
        conn.execute("DELETE FROM collections WHERE name=?", (name,))
        conn.executemany(
            "INSERT INTO collections(name,item_id,payload,position) VALUES(?,?,?,?)",
            [
                (
                    name,
                    str(item.get("id") or f"row-{index}"),
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    index,
                )
                for index, item in enumerate(items)
            ],
        )


def mutate_collection(name: str, mutator) -> Any:
    """Atomically read, mutate and rewrite one collection.

    The BEGIN IMMEDIATE transaction prevents concurrent read-modify-write callers
    from overwriting each other's updates, including callers in other processes.
    The mutator receives the current list and may change it in place.
    """
    init_db()
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT payload FROM collections WHERE name=? ORDER BY position ASC",
            (name,),
        ).fetchall()
        items = [json.loads(row["payload"]) for row in rows]
        result = mutator(items)
        conn.execute("DELETE FROM collections WHERE name=?", (name,))
        conn.executemany(
            "INSERT INTO collections(name,item_id,payload,position) VALUES(?,?,?,?)",
            [
                (
                    name,
                    str(item.get("id") or f"row-{index}"),
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    index,
                )
                for index, item in enumerate(items)
            ],
        )
        return result


def collection_empty(name: str) -> bool:
    init_db()
    with _LOCK, connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM collections WHERE name=? LIMIT 1", (name,)
        ).fetchone()
    return row is None


def import_legacy_json(name: str, path: Path) -> int:
    if not collection_empty(name) or not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(value, list):
        return 0
    items = [item for item in value if isinstance(item, dict)]
    save_collection(name, items)
    return len(items)


def append_event(event: dict[str, Any]) -> None:
    init_db()
    with _LOCK, connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO events(id,type,created_at,run_id,payload) VALUES(?,?,?,?,?)",
            (
                str(event["id"]),
                str(event["type"]),
                str(event["created_at"]),
                str(event.get("run_id") or "") or None,
                json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            ),
        )


def query_events(*, limit: int = 200, event_type: str | None = None,
                 run_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if event_type:
        clauses.append("type=?")
        params.append(event_type)
    if run_id:
        clauses.append("run_id=?")
        params.append(run_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"SELECT payload FROM events{where} ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    with _LOCK, connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [json.loads(row["payload"]) for row in rows]
