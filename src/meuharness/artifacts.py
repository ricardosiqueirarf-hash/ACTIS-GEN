"""Persistent artifacts created by ACTIS agents and exposed in chat."""
from __future__ import annotations

import mimetypes
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.storage import connect, init_db

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _init() -> None:
    init_db()
    with _LOCK, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_id TEXT,
                source_run_id TEXT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, path)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_run_created
            ON artifacts(run_id, created_at ASC);
            """
        )


def _allowed_roots() -> list[Path]:
    roots = [Path.home().resolve()]
    for key in ("ACTIS_ARTIFACT_ROOT", "ACTIS_SPREADSHEET_ROOT"):
        value = os.getenv(key, "").strip()
        if value:
            root = Path(value).expanduser().resolve()
            if root not in roots:
                roots.append(root)
    return roots


def _safe_file(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    if not any(candidate == root or root in candidate.parents for root in _allowed_roots()):
        raise PermissionError("Artifact fora dos diretórios permitidos do ACTIS.")
    return candidate


def register_artifact(
    path: str | Path,
    *,
    run_id: str,
    agent_id: str | None = None,
    source_run_id: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Register or refresh one file produced during a run."""
    file_path = _safe_file(path)
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("run_id é obrigatório para registrar artifact.")
    filename = str(name or file_path.name).strip()[:240] or file_path.name
    media = str(mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    now = _now()
    _init()
    with _LOCK, connect() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM artifacts WHERE run_id=? AND path=?",
            (run_id, str(file_path)),
        ).fetchone()
        artifact_id = str(existing["id"]) if existing else uuid4().hex
        created_at = str(existing["created_at"]) if existing else now
        conn.execute(
            """
            INSERT INTO artifacts(id,run_id,agent_id,source_run_id,name,path,mime_type,size,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id,path) DO UPDATE SET
                agent_id=excluded.agent_id,
                source_run_id=excluded.source_run_id,
                name=excluded.name,
                mime_type=excluded.mime_type,
                size=excluded.size,
                updated_at=excluded.updated_at
            """,
            (
                artifact_id, run_id, agent_id, source_run_id, filename, str(file_path), media,
                file_path.stat().st_size, created_at, now,
            ),
        )
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
    return dict(row)


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    _init()
    with _LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (str(artifact_id),)).fetchone()
    return dict(row) if row else None


def artifact_file(artifact_id: str) -> tuple[dict[str, Any], Path]:
    artifact = get_artifact(artifact_id)
    if not artifact:
        raise FileNotFoundError("Artifact não encontrado.")
    return artifact, _safe_file(str(artifact["path"]))


def list_run_artifacts(run_id: str) -> list[dict[str, Any]]:
    _init()
    with _LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at ASC",
            (str(run_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def as_attachment(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "file",
        "artifact_id": artifact["id"],
        "name": artifact["name"],
        "media_type": artifact["mime_type"],
        "size": int(artifact.get("size") or 0),
        "download_url": f"/api/artifacts/{artifact['id']}/download",
    }


def list_run_attachments(run_id: str) -> list[dict[str, Any]]:
    return [as_attachment(item) for item in list_run_artifacts(run_id)]


def bubble_run_artifacts(source_run_id: str, target_run_id: str, target_agent_id: str | None) -> list[dict[str, Any]]:
    """Expose artifacts from a delegated child run on the parent run as well."""
    bubbled: list[dict[str, Any]] = []
    for item in list_run_artifacts(source_run_id):
        bubbled.append(
            register_artifact(
                item["path"],
                run_id=target_run_id,
                agent_id=target_agent_id,
                source_run_id=source_run_id,
                name=item["name"],
                mime_type=item["mime_type"],
            )
        )
    return bubbled
