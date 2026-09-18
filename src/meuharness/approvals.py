"""Granular approval queue for ACTIS GEN capabilities."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from meuharness.event_bus import emit_event
from meuharness.storage import connect, init_db
from meuharness.tasks import update_task_by_run

TOOL_SCOPES = {
    "harness_browser": ("browser.read", "browser.interact"),
    "harness_files": ("files.read", "files.write"),
    "harness_terminal": ("terminal.exec",),
    "harness_computer": ("computer.view", "computer.control"),
    "pdf_toolkit": ("pdf.read", "pdf.write"),
    "excel_toolkit": ("spreadsheet.read", "spreadsheet.write"),
    "actis_admin": ("actis.admin",),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def request_approval(agent_id: str, scope: str, reason: str = "") -> dict:
    scope = str(scope or "").strip()
    if not scope:
        raise ValueError("Escopo de aprovação obrigatório.")
    init_db()
    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM approvals WHERE agent_id=? AND scope=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (agent_id, scope),
        ).fetchone()
    if existing:
        return dict(existing)
    approval = {
        "id": uuid4().hex,
        "agent_id": agent_id,
        "scope": scope,
        "status": "pending",
        "reason": str(reason or "").strip()[:1000],
        "created_at": _now(),
        "resolved_at": None,
    }
    with connect() as conn:
        conn.execute(
            "INSERT INTO approvals(id,agent_id,scope,status,reason,created_at,resolved_at) VALUES(?,?,?,?,?,?,?)",
            tuple(approval[k] for k in ("id","agent_id","scope","status","reason","created_at","resolved_at")),
        )
    return approval


def list_approvals(status: str | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM approvals"
    params: tuple = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def resolve_approval(approval_id: str, approved: bool) -> dict:
    status = "approved" if approved else "denied"
    resolved_at = _now()
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE approvals SET status=?,resolved_at=? WHERE id=? AND status='pending'",
            (status, resolved_at, approval_id),
        )
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        raise ValueError("Approval não encontrado.")
    approval = dict(row)
    match = re.search(r"run ([0-9a-f-]+)", str(approval.get("reason") or ""), re.IGNORECASE)
    if match:
        run_id = match.group(1)
        pending_same_run = [a for a in list_approvals("pending") if run_id in str(a.get("reason") or "")]
        if not pending_same_run:
            if approved:
                update_task_by_run(run_id, status="completed", activity="completed", detail="Aprovação concedida", tool=None, finished_at=resolved_at)
                emit_event("agent.state", run_id=run_id, agent_id=approval.get("agent_id"), state="completed", detail="Aprovação concedida")
            else:
                update_task_by_run(run_id, status="blocked", activity="blocked", detail="Aprovação negada", tool=None)
                emit_event("agent.state", run_id=run_id, agent_id=approval.get("agent_id"), state="blocked", detail="Aprovação negada")
    emit_event("approval.resolved", approval_id=approval_id, agent_id=approval.get("agent_id"), scope=approval.get("scope"), status=status)
    return approval


def approved_scopes(agent_id: str) -> set[str]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT scope FROM approvals WHERE agent_id=? AND status='approved'",
            (agent_id,),
        ).fetchall()
    return {str(row["scope"]) for row in rows}


def scopes_for_tool(tool_id: str) -> tuple[str, ...]:
    return TOOL_SCOPES.get(tool_id, ())


def tool_for_scope(scope: str) -> str | None:
    for tool_id, scopes in TOOL_SCOPES.items():
        if scope in scopes:
            return tool_id
    return None

def pending_for_agent(agent_id: str) -> list[dict]:
    return [a for a in list_approvals("pending") if a.get("agent_id") == agent_id]


def revoke_approval(approval_id: str) -> dict:
    init_db()
    resolved_at = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE approvals SET status='revoked',resolved_at=? WHERE id=? AND status='approved'",
            (resolved_at, approval_id),
        )
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        raise ValueError("Approval não encontrado.")
    return dict(row)
