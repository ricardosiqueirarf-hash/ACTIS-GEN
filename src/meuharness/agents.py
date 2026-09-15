"""Persistent local registry for ACTIS GEN agent definitions and runs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.storage import DATA_DIR, import_legacy_json, load_collection, save_collection
from meuharness.tool_registry import normalize_scopes, normalize_tool_ids, scopes_for_tools

AGENTS_FILE = DATA_DIR / "agents.json"
RUNS_FILE = DATA_DIR / "runs.json"
COMPANIES_FILE = DATA_DIR / "companies.json"
SECTORS_FILE = DATA_DIR / "sectors.json"
CONVERSATIONS_FILE = DATA_DIR / "conversations.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _collection_name(path: Path) -> str:
    return path.stem


def _read(path: Path) -> list[dict[str, Any]]:
    name = _collection_name(path)
    import_legacy_json(name, path)
    return load_collection(name)


def _write(path: Path, value: list[dict[str, Any]]) -> None:
    save_collection(_collection_name(path), value)


def _new_id(name: str, existing: list[dict[str, Any]], fallback: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or fallback
    value = base
    while any(item.get("id") == value for item in existing):
        value = f"{base}-{uuid4().hex[:4]}"
    return value


def list_companies() -> list[dict[str, Any]]:
    return _read(COMPANIES_FILE)


def get_company(company_id: str) -> dict[str, Any] | None:
    return next((c for c in list_companies() if c.get("id") == company_id), None)


def create_company(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Nome da empresa é obrigatório.")
    companies = list_companies()
    company = {"id": _new_id(name, companies, "empresa"), "name": name, "created_at": _now(), "updated_at": _now()}
    companies.append(company)
    _write(COMPANIES_FILE, companies)
    return company


def update_company(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    companies = list_companies()
    for company in companies:
        if company.get("id") == company_id:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("Nome da empresa é obrigatório.")
            company["name"] = name
            company["updated_at"] = _now()
            _write(COMPANIES_FILE, companies)
            return company
    raise ValueError("Empresa não encontrada.")


def list_sectors(company_id: str | None = None) -> list[dict[str, Any]]:
    sectors = _read(SECTORS_FILE)
    return [s for s in sectors if s.get("company_id") == company_id] if company_id else sectors


def get_sector(sector_id: str) -> dict[str, Any] | None:
    return next((s for s in list_sectors() if s.get("id") == sector_id), None)


def create_sector(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    company_id = str(data.get("company_id") or "").strip()
    if not name:
        raise ValueError("Nome do setor é obrigatório.")
    if not company_id or not get_company(company_id):
        raise ValueError("Todo setor precisa pertencer a uma empresa válida.")
    sectors = list_sectors()
    sector = {"id": _new_id(name, sectors, "setor"), "name": name, "company_id": company_id, "created_at": _now(), "updated_at": _now()}
    sectors.append(sector)
    _write(SECTORS_FILE, sectors)
    return sector


def update_sector(sector_id: str, data: dict[str, Any]) -> dict[str, Any]:
    sectors = list_sectors()
    for sector in sectors:
        if sector.get("id") == sector_id:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValueError("Nome do setor é obrigatório.")
            sector["name"] = name
            sector["updated_at"] = _now()
            _write(SECTORS_FILE, sectors)
            return sector
    raise ValueError("Setor não encontrado.")


def _validated_org(company_id: Any, sector_id: Any) -> tuple[str | None, str | None]:
    company_id = str(company_id or "").strip() or None
    sector_id = str(sector_id or "").strip() or None
    if sector_id:
        sector = get_sector(sector_id)
        if not sector:
            raise ValueError("Setor não encontrado.")
        sector_company = str(sector.get("company_id") or "")
        if company_id and company_id != sector_company:
            raise ValueError("O setor selecionado não pertence à empresa selecionada.")
        company_id = sector_company
    if company_id and not get_company(company_id):
        raise ValueError("Empresa não encontrada.")
    return company_id, sector_id


def list_agents() -> list[dict[str, Any]]:
    return _read(AGENTS_FILE)


def get_agent(agent_id: str) -> dict[str, Any] | None:
    return next((a for a in list_agents() if a.get("id") == agent_id), None)


def create_agent(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Nome do agente é obrigatório.")
    agents = list_agents()
    company_id, sector_id = _validated_org(data.get("company_id"), data.get("sector_id"))
    tools = normalize_tool_ids(list(data.get("tools") or []))
    permissions = (
        normalize_scopes(list(data.get("permissions") or []))
        if "permissions" in data
        else scopes_for_tools(tools)
    )
    allowed_scopes = set(scopes_for_tools(tools))
    permissions = [scope for scope in permissions if scope in allowed_scopes]
    agent = {
        "id": _new_id(name, agents, "agent"),
        "name": name,
        "icon": str(data.get("icon") or "◆")[:4],
        "avatar_color": str(data.get("avatar_color") or "#d7ff64")[:16],
        "avatar_shape": str(data.get("avatar_shape") or "rounded")[:16],
        "company_id": company_id,
        "sector_id": sector_id,
        "model": str(data.get("model") or "").strip(),
        "instructions": str(data.get("instructions") or "").strip(),
        "tools": tools,
        "permissions": permissions,
        "memory": bool(data.get("memory", True)),
        "status": "ready",
        "created_at": _now(),
        "updated_at": _now(),
    }
    agents.append(agent)
    _write(AGENTS_FILE, agents)
    return agent


def update_agent(agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
    agents = list_agents()
    for agent in agents:
        if agent.get("id") == agent_id:
            for key in ("name", "icon", "avatar_color", "avatar_shape", "model", "instructions"):
                if key in data:
                    agent[key] = str(data[key]).strip()
            if "company_id" in data or "sector_id" in data:
                company_id = data.get("company_id", agent.get("company_id"))
                sector_id = data.get("sector_id", agent.get("sector_id"))
                if "company_id" in data and "sector_id" not in data:
                    current_sector = get_sector(str(sector_id or "")) if sector_id else None
                    if current_sector and current_sector.get("company_id") != (str(company_id or "").strip() or None):
                        sector_id = None
                company_id, sector_id = _validated_org(company_id, sector_id)
                agent["company_id"] = company_id
                agent["sector_id"] = sector_id
            if "tools" in data:
                agent["tools"] = normalize_tool_ids(list(data.get("tools") or []))
                if "permissions" not in data:
                    agent["permissions"] = scopes_for_tools(agent["tools"])
            if "permissions" in data:
                allowed = set(scopes_for_tools(list(agent.get("tools") or [])))
                agent["permissions"] = [
                    scope for scope in normalize_scopes(list(data.get("permissions") or []))
                    if scope in allowed
                ]
            if "memory" in data:
                agent["memory"] = bool(data["memory"])
            agent["updated_at"] = _now()
            _write(AGENTS_FILE, agents)
            return agent
    raise ValueError("Agente não encontrado.")


def list_conversations(agent_id: str | None = None) -> list[dict[str, Any]]:
    conversations = _read(CONVERSATIONS_FILE)
    if agent_id:
        conversations = [c for c in conversations if c.get("agent_id") == agent_id]
    return sorted(conversations, key=lambda c: c.get("updated_at", ""), reverse=True)


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    return next((c for c in _read(CONVERSATIONS_FILE) if c.get("id") == conversation_id), None)


def create_conversation(agent_id: str, title: str | None = None) -> dict[str, Any]:
    if not get_agent(agent_id):
        raise ValueError("Agente não encontrado.")
    conversations = _read(CONVERSATIONS_FILE)
    now = _now()
    conversation = {
        "id": uuid4().hex,
        "agent_id": agent_id,
        "title": str(title or "Nova conversa").strip()[:80] or "Nova conversa",
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    conversations.append(conversation)
    _write(CONVERSATIONS_FILE, conversations)
    return conversation


def append_conversation_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    run_id: str | None = None,
    status: str = "ok",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    role = str(role or "").strip()
    if role not in {"user", "assistant", "system"}:
        raise ValueError("Role de mensagem inválido.")
    content = str(content or "").strip()
    if not content:
        raise ValueError("Mensagem vazia.")
    conversations = _read(CONVERSATIONS_FILE)
    for conversation in conversations:
        if conversation.get("id") != conversation_id:
            continue
        messages = conversation.setdefault("messages", [])
        message = {
            "id": uuid4().hex,
            "role": role,
            "content": content,
            "run_id": run_id,
            "status": status,
            "created_at": _now(),
        }
        if attachments:
            message["attachments"] = list(attachments)
        messages.append(message)
        if role == "user" and (conversation.get("title") in {None, "", "Nova conversa"}):
            clean = re.sub(r"\s+", " ", content).strip()
            conversation["title"] = clean[:52] + ("…" if len(clean) > 52 else "")
        conversation["updated_at"] = _now()
        _write(CONVERSATIONS_FILE, conversations)
        return message
    raise ValueError("Conversa não encontrada.")


def list_runs(agent_id: str | None = None) -> list[dict[str, Any]]:
    runs = _read(RUNS_FILE)
    if agent_id:
        runs = [r for r in runs if r.get("agent_id") == agent_id]
    return sorted(runs, key=lambda r: r.get("started_at", ""), reverse=True)


def start_run(
    agent_id: str,
    prompt: str,
    model: str,
    *,
    source: str = "user",
    parent_agent_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    runs = _read(RUNS_FILE)
    run = {
        "id": uuid4().hex,
        "agent_id": agent_id,
        "prompt": prompt,
        "model": model,
        "status": "running",
        "source": source,
        "parent_agent_id": parent_agent_id,
        "conversation_id": conversation_id,
        "started_at": _now(),
        "finished_at": None,
        "elapsed_ms": None,
        "response": None,
        "error": None,
    }
    runs.append(run)
    _write(RUNS_FILE, runs)
    return run


def cancel_run_record(run_id: str, reason: str = "Cancelado pelo usuário.") -> bool:
    runs = _read(RUNS_FILE)
    changed = False
    for run in runs:
        if run.get("id") == run_id and run.get("status") == "running":
            run.update({
                "status": "cancelled",
                "finished_at": _now(),
                "error": reason,
            })
            changed = True
            break
    if changed:
        _write(RUNS_FILE, runs)
    return changed


def cancel_stale_running_runs(reason: str = "Execução interrompida pela reinicialização do ACTIS GEN.") -> int:
    runs = _read(RUNS_FILE)
    changed = 0
    for run in runs:
        if run.get("status") == "running":
            run.update({"status": "cancelled", "finished_at": _now(), "error": reason})
            changed += 1
    if changed:
        _write(RUNS_FILE, runs)
    return changed


def finish_run(
    run_id: str,
    *,
    response: str | None = None,
    elapsed_ms: float | None = None,
    error: str | None = None,
) -> None:
    runs = _read(RUNS_FILE)
    for run in runs:
        if run.get("id") == run_id:
            run.update(
                {
                    "status": "failed" if error else "completed",
                    "finished_at": _now(),
                    "elapsed_ms": elapsed_ms,
                    "response": response,
                    "error": error,
                }
            )
            break
    _write(RUNS_FILE, runs)
