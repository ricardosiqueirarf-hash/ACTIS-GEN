"""Hierarchical context spaces for ACTIS GEN agents."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.storage import DATA_DIR, import_legacy_json, load_collection, save_collection
from meuharness.tenant import (
    assert_organization_access,
    current_organization_id,
    entity_organization_id,
    filter_by_organization,
    normalize_organization_id,
    resolve_inherited_organization_id,
)

CONTEXT_ITEMS_FILE = DATA_DIR / "context_items.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
BINDINGS_FILE = DATA_DIR / "agent_context_bindings.json"
VALID_SCOPES = {"company", "sector", "project", "agent"}
VALID_KINDS = {"instruction", "knowledge", "document", "fact", "note", "procedure", "state", "reference"}


def _scope_organization(scope_type: str, scope_id: str) -> str | None:
    if scope_type == "company":
        from meuharness.agents import get_company

        company = get_company(scope_id)
        return entity_organization_id(company, company_is_tenant=False) if company else None
    if scope_type == "sector":
        from meuharness.agents import get_sector

        sector = get_sector(scope_id)
        company_id = str((sector or {}).get("company_id") or "")
        from meuharness.agents import get_company

        company = get_company(company_id) if company_id else None
        return entity_organization_id(company, company_is_tenant=False) if company else None
    if scope_type == "project":
        project = get_project(scope_id)
        return normalize_organization_id((project or {}).get("organization_id"))
    if scope_type == "agent":
        from meuharness.agents import get_agent

        agent = get_agent(scope_id)
        return entity_organization_id(agent) if agent else None
    return None


def _assert_context_access(item: dict[str, Any]) -> None:
    organization_id = current_organization_id()
    if organization_id:
        assert_organization_access(item, organization_id, resource="Contexto")


def get_project(project_id: str) -> dict[str, Any] | None:
    projects = _read(PROJECTS_FILE)
    project = next((item for item in projects if item.get("id") == project_id), None)
    if project:
        _assert_context_access(project)
    return project


def _binding_organization(binding: dict[str, Any]) -> str | None:
    return normalize_organization_id(binding.get("organization_id"))


def _assert_binding_access(binding: dict[str, Any]) -> None:
    organization_id = current_organization_id()
    if organization_id:
        assert_organization_access(binding, organization_id, resource="Binding de contexto")


def _read_filtered(path: Path) -> list[dict[str, Any]]:
    return filter_by_organization(_read(path))


def _now() -> str:
    return datetime.now(UTC).isoformat()

def _read(path: Path) -> list[dict[str, Any]]:
    name = path.stem
    import_legacy_json(name, path)
    return load_collection(name)

def _write(path: Path, value: list[dict[str, Any]]) -> None:
    save_collection(path.stem, value)

def list_context_items(scope_type: str | None = None, scope_id: str | None = None) -> list[dict[str, Any]]:
    items = _read(CONTEXT_ITEMS_FILE)
    if scope_type:
        items = [item for item in items if item.get("scope_type") == scope_type]
    if scope_id:
        items = [item for item in items if item.get("scope_id") == scope_id]
    items = filter_by_organization(items)
    return sorted(items, key=lambda item: (not bool(item.get("pinned")), -int(item.get("priority", 0)), item.get("title", "")))


def create_context_item(data: dict[str, Any]) -> dict[str, Any]:
    scope_type = str(data.get("scope_type") or "").strip().lower()
    scope_id = str(data.get("scope_id") or "").strip()
    kind = str(data.get("kind") or "knowledge").strip().lower()
    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    if scope_type not in VALID_SCOPES:
        raise ValueError("Escopo de contexto inválido.")
    if not scope_id or not title or not content:
        raise ValueError("Escopo, título e conteúdo são obrigatórios.")
    if kind not in VALID_KINDS:
        raise ValueError("Tipo de contexto inválido.")
    organization_id = resolve_inherited_organization_id(data.get("organization_id"))
    scope_organization_id = _scope_organization(scope_type, scope_id)
    if organization_id and scope_organization_id and organization_id != scope_organization_id:
        raise PermissionError("Escopo pertence a outra organization.")
    organization_id = organization_id or scope_organization_id
    now = _now()
    item = {
        "id": uuid4().hex,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "kind": kind,
        "title": title[:160],
        "content": content,
        "pinned": bool(data.get("pinned", False)),
        "priority": max(0, min(100, int(data.get("priority", 50)))),
        "source": str(data.get("source") or "manual").strip()[:120],
        "active": bool(data.get("active", True)),
        "created_at": now,
        "updated_at": now,
    }
    if organization_id:
        item["organization_id"] = organization_id
    items = _read(CONTEXT_ITEMS_FILE)
    items.append(item)
    _write(CONTEXT_ITEMS_FILE, items)
    return item


def get_context_item(item_id: str) -> dict[str, Any] | None:
    item = next((value for value in _read(CONTEXT_ITEMS_FILE) if value.get("id") == item_id), None)
    if item:
        _assert_context_access(item)
    return item


def delete_context_item(item_id: str) -> bool:
    item = get_context_item(item_id)
    if not item:
        return False
    items = _read(CONTEXT_ITEMS_FILE)
    kept = [value for value in items if value.get("id") != item_id]
    if len(kept) == len(items):
        return False
    _write(CONTEXT_ITEMS_FILE, kept)
    return True


def list_projects(company_id: str | None = None) -> list[dict[str, Any]]:
    projects = _read(PROJECTS_FILE)
    if company_id:
        projects = [project for project in projects if project.get("company_id") == company_id]
    return sorted(filter_by_organization(projects), key=lambda project: project.get("name", ""))


def create_project(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    company_id = str(data.get("company_id") or "").strip()
    if not name or not company_id:
        raise ValueError("Projeto precisa de nome e empresa.")
    company = get_company(company_id)
    if not company:
        raise ValueError("Empresa não encontrada.")
    organization_id = resolve_inherited_organization_id(data.get("organization_id"))
    company_organization_id = entity_organization_id(company, company_is_tenant=False)
    if organization_id and company_organization_id and organization_id != company_organization_id:
        raise PermissionError("Projeto pertence a outra organization.")
    organization_id = organization_id or company_organization_id
    now = _now()
    project = {
        "id": uuid4().hex,
        "name": name[:160],
        "company_id": company_id,
        "status": str(data.get("status") or "active"),
        "created_at": now,
        "updated_at": now,
    }
    if organization_id:
        project["organization_id"] = organization_id
    projects = _read(PROJECTS_FILE)
    projects.append(project)
    _write(PROJECTS_FILE, projects)
    return project


def list_agent_bindings(agent_id: str | None = None) -> list[dict[str, Any]]:
    bindings = filter_by_organization(_read(BINDINGS_FILE))
    return [binding for binding in bindings if binding.get("agent_id") == agent_id] if agent_id else bindings


def set_agent_project_bindings(agent_id: str, project_ids: list[str]) -> list[dict[str, Any]]:
    from meuharness.agents import get_agent

    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado.")
    current_organization_id_value = current_organization_id()
    agent_organization_id = entity_organization_id(agent)
    if current_organization_id_value and agent_organization_id and current_organization_id_value != agent_organization_id:
        raise PermissionError("Agente pertence a outra organization.")
    organization_id = current_organization_id_value or agent_organization_id
    projects = {project["id"]: project for project in list_projects()}
    clean = [str(project_id) for project_id in project_ids if str(project_id) in projects]
    for project_id in clean:
        project_organization_id = normalize_organization_id(projects[project_id].get("organization_id"))
        if organization_id and project_organization_id and project_organization_id != organization_id:
            raise PermissionError("Projeto pertence a outra organization.")
    bindings = [
        binding
        for binding in _read(BINDINGS_FILE)
        if not (binding.get("agent_id") == agent_id and binding.get("scope_type") == "project")
    ]
    now = _now()
    for project_id in dict.fromkeys(clean):
        binding = {
            "id": uuid4().hex,
            "agent_id": agent_id,
            "scope_type": "project",
            "scope_id": project_id,
            "permission": "read",
            "created_at": now,
        }
        if organization_id:
            binding["organization_id"] = organization_id
        bindings.append(binding)
    _write(BINDINGS_FILE, bindings)
    return list_agent_bindings(agent_id)


def _terms(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-ZÀ-ÿ0-9_-]{3,}", text.lower())}


def resolve_agent_context(agent: dict[str, Any], prompt: str, max_retrieved: int = 8) -> dict[str, Any]:
    current_organization_id_value = current_organization_id()
    agent_organization_id = entity_organization_id(agent)
    if current_organization_id_value and agent.get("id") != "general":
        assert_organization_access(agent, current_organization_id_value, resource="Agente")
    organization_id = current_organization_id_value or agent_organization_id
    scopes = []
    if agent.get("company_id"):
        scopes.append(("company", str(agent["company_id"])))
    if agent.get("sector_id"):
        scopes.append(("sector", str(agent["sector_id"])))
    bindings = [
        binding
        for binding in filter_by_organization(_read(BINDINGS_FILE), organization_id)
        if binding.get("agent_id") == agent.get("id") and binding.get("scope_type") == "project"
    ]
    for binding in bindings:
        scopes.append(("project", str(binding.get("scope_id") or "")))
    scopes.append(("agent", str(agent.get("id") or "")))
    scope_set = set(scopes)
    candidates = [
        item
        for item in filter_by_organization(_read(CONTEXT_ITEMS_FILE), organization_id)
        if item.get("active", True) and (item.get("scope_type"), item.get("scope_id")) in scope_set
    ]
    pinned = [item for item in candidates if item.get("pinned")]
    query = _terms(prompt)
    scored = []
    for item in candidates:
        if item.get("pinned"):
            continue
        hay = _terms(str(item.get("title") or "") + " " + str(item.get("content") or ""))
        overlap = len(query & hay)
        if overlap == 0 and int(item.get("priority", 50)) < 90:
            continue
        scored.append((overlap * 10 + int(item.get("priority", 50)), item))
    scored.sort(key=lambda value: value[0], reverse=True)
    retrieved = [item for score, item in scored[:max_retrieved] if score > 0]
    return {"scopes": [{"type": scope_type, "id": scope_id} for scope_type, scope_id in scopes], "pinned": pinned, "retrieved": retrieved}


def context_instructions(package: dict[str, Any]) -> str:
    items=list(package.get("pinned") or [])+list(package.get("retrieved") or [])
    if not items: return ""
    lines=["\n\nCONTEXTO ORGANIZACIONAL HERDADO DO ACTIS GEN:"]
    for item in items:
        lines.append(f"- [{item.get('scope_type')}/{item.get('kind')}] {item.get('title')}: {item.get('content')}")
    lines.append("Use este contexto quando for relevante. Regras mais específicas do run/conversa têm precedência sobre contexto organizacional genérico.")
    return "\n".join(lines)
