"""Persistent ACTIS GEN automations registry."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from meuharness.agents import DATA_DIR, get_agent
from meuharness.storage import import_legacy_json, load_collection, save_collection
from meuharness.tenant import (
    assert_organization_access,
    current_organization_id,
    entity_organization_id,
    filter_by_organization,
    normalize_organization_id,
    resolve_inherited_organization_id,
)

AUTOMATIONS_FILE = DATA_DIR / "automations.json"
_LOCK = threading.RLock()


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat()


def _read() -> list[dict[str, Any]]:
    with _LOCK:
        import_legacy_json("automations", AUTOMATIONS_FILE)
        return load_collection("automations")


def _write(value: list[dict[str, Any]]) -> None:
    with _LOCK:
        save_collection("automations", value)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value)
        dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _next_daily(hhmm: str, after: datetime | None = None) -> datetime:
    after = after or _now_dt()
    local = after.astimezone()
    try:
        hour, minute = [int(x) for x in hhmm.split(":", 1)]
    except Exception as exc:
        raise ValueError("Horário diário inválido. Use HH:MM.") from exc
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _assert_automation_access(automation: dict[str, Any]) -> None:
    organization_id = current_organization_id()
    if organization_id:
        assert_organization_access(
            automation,
            organization_id,
            resource="Automação",
            allow_unscoped=True,
        )


def _automation_organization(automation: dict[str, Any]) -> str | None:
    return normalize_organization_id(automation.get("organization_id"))


def _read_visible() -> list[dict[str, Any]]:
    return filter_by_organization(_read(), include_unscoped=True)


def _normalized(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(existing or {})
    agent_id = str(data.get("agent_id", base.get("agent_id", "")) or "").strip()
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente da automação não encontrado.")
    organization_id = resolve_inherited_organization_id(data.get("organization_id"), existing)
    agent_organization_id = entity_organization_id(agent)
    if organization_id and agent_organization_id and organization_id != agent_organization_id:
        raise PermissionError("Automação pertence a outra organization.")
    action = str(data.get("action", base.get("action", "")) or "").strip()
    if not action:
        raise ValueError("Ação da automação é obrigatória.")
    trigger_type = str(data.get("trigger_type", base.get("trigger_type", "interval")) or "interval").strip().lower()
    if trigger_type not in {"interval", "daily", "once", "condition"}:
        raise ValueError("Tipo de gatilho inválido.")
    base.update({
        "name": str(data.get("name", base.get("name", "Automação")) or "Automação").strip()[:90] or "Automação",
        "agent_id": agent_id,
        "action": action,
        "trigger_type": trigger_type,
        "enabled": bool(data.get("enabled", base.get("enabled", True))),
    })
    if organization_id:
        base["organization_id"] = organization_id
    elif "organization_id" in base:
        base.pop("organization_id", None)
    if trigger_type == "interval":
        seconds = int(data.get("interval_seconds", base.get("interval_seconds", 3600)) or 3600)
        base["interval_seconds"] = max(60, seconds)
        base["daily_time"] = None; base["once_at"] = None; base["condition"] = None
    elif trigger_type == "daily":
        hhmm = str(data.get("daily_time", base.get("daily_time", "09:00")) or "09:00").strip()
        _next_daily(hhmm)
        base["daily_time"] = hhmm
        base["interval_seconds"] = None; base["once_at"] = None; base["condition"] = None
    elif trigger_type == "once":
        once = str(data.get("once_at", base.get("once_at", "")) or "").strip()
        if not _parse_iso(once):
            raise ValueError("Data/hora única inválida.")
        base["once_at"] = once
        base["interval_seconds"] = None; base["daily_time"] = None; base["condition"] = None
    else:
        condition = str(data.get("condition", base.get("condition", "")) or "").strip()
        if not condition:
            raise ValueError("Condição da automação é obrigatória.")
        check_seconds = int(data.get("check_seconds", base.get("check_seconds", 300)) or 300)
        base["condition"] = condition
        base["check_seconds"] = max(60, check_seconds)
        base["interval_seconds"] = None; base["daily_time"] = None; base["once_at"] = None
    return base


def _next_for(auto: dict[str, Any], after: datetime | None = None) -> str | None:
    if not auto.get("enabled", True):
        return None
    after = after or _now_dt()
    kind = auto.get("trigger_type")
    if kind == "interval":
        return (after + timedelta(seconds=int(auto.get("interval_seconds") or 3600))).isoformat()
    if kind == "daily":
        return _next_daily(str(auto.get("daily_time") or "09:00"), after).isoformat()
    if kind == "once":
        dt = _parse_iso(str(auto.get("once_at") or ""))
        return dt.isoformat() if dt else None
    if kind == "condition":
        return (after + timedelta(seconds=int(auto.get("check_seconds") or 300))).isoformat()
    return None


def list_automations() -> list[dict[str, Any]]:
    return sorted(_read(), key=lambda x: x.get("created_at", ""), reverse=True)


def get_automation(automation_id: str) -> dict[str, Any] | None:
    return next((a for a in _read() if a.get("id") == automation_id), None)


def create_automation(data: dict[str, Any]) -> dict[str, Any]:
    automations = _read()
    now = _now()
    auto = _normalized(data)
    auto.update({"id": uuid4().hex, "created_at": now, "updated_at": now, "last_run_at": None,
                 "last_check_at": None, "last_status": "never", "last_error": None,
                 "last_response": None, "last_run_id": None, "run_count": 0,
                 "condition_active": False, "scheduler_state": "idle"})
    auto["next_run_at"] = _next_for(auto, _now_dt() - timedelta(seconds=1))
    automations.append(auto); _write(automations); return auto


def update_automation(automation_id: str, data: dict[str, Any]) -> dict[str, Any]:
    automations = _read()
    for i, auto in enumerate(automations):
        if auto.get("id") != automation_id: continue
        updated = _normalized(data, auto)
        updated["updated_at"] = _now()
        if any(k in data for k in ("trigger_type","interval_seconds","daily_time","once_at","condition","check_seconds","enabled")):
            updated["next_run_at"] = _next_for(updated)
            updated["scheduler_state"] = "idle"
        automations[i] = updated; _write(automations); return updated
    raise ValueError("Automação não encontrada.")


def delete_automation(automation_id: str) -> bool:
    automations = _read(); kept = [a for a in automations if a.get("id") != automation_id]
    if len(kept) == len(automations): return False
    _write(kept); return True


def claim_due_automations(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now_dt(); automations = _read(); due = []; changed = False
    for auto in automations:
        if not auto.get("enabled", True) or auto.get("scheduler_state") in {"running","checking"}: continue
        nxt = _parse_iso(auto.get("next_run_at"))
        if not nxt or nxt > now: continue
        auto["scheduler_state"] = "checking" if auto.get("trigger_type") == "condition" else "running"
        auto["last_check_at"] = now.isoformat()
        kind = auto.get("trigger_type")
        if kind == "once": auto["next_run_at"] = None
        else: auto["next_run_at"] = _next_for(auto, now)
        due.append(dict(auto)); changed = True
    if changed: _write(automations)
    return due


def record_condition(automation_id: str, active: bool, detail: str = "") -> tuple[dict[str, Any] | None, bool]:
    automations = _read()
    for auto in automations:
        if auto.get("id") != automation_id: continue
        previous = bool(auto.get("condition_active", False)); should_trigger = active and not previous
        auto["condition_active"] = active; auto["scheduler_state"] = "running" if should_trigger else "idle"
        auto["last_status"] = "condition_true" if active else "waiting"
        if detail: auto["last_response"] = detail[:1200]
        auto["updated_at"] = _now(); _write(automations); return dict(auto), should_trigger
    return None, False


def record_result(automation_id: str, *, status: str, response: str | None = None,
                  error: str | None = None, run_id: str | None = None) -> None:
    automations = _read(); now = _now()
    for auto in automations:
        if auto.get("id") != automation_id: continue
        auto["last_run_at"] = now; auto["last_status"] = status; auto["last_error"] = error
        auto["last_response"] = response; auto["last_run_id"] = run_id
        auto["run_count"] = int(auto.get("run_count") or 0) + (1 if status in {"completed","failed"} else 0)
        auto["scheduler_state"] = "idle"; auto["updated_at"] = now
        if auto.get("trigger_type") == "once": auto["enabled"] = False
        _write(automations); return


def release_claim(automation_id: str, error: str | None = None) -> None:
    automations = _read()
    for auto in automations:
        if auto.get("id") == automation_id:
            auto["scheduler_state"] = "idle"; auto["last_error"] = error; auto["updated_at"] = _now(); _write(automations); return
