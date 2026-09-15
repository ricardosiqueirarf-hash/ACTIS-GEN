"""Hierarchical context spaces for ACTIS GEN agents."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.storage import DATA_DIR, import_legacy_json, load_collection, save_collection

CONTEXT_ITEMS_FILE = DATA_DIR / "context_items.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
BINDINGS_FILE = DATA_DIR / "agent_context_bindings.json"
VALID_SCOPES = {"company", "sector", "project", "agent"}
VALID_KINDS = {"instruction", "knowledge", "document", "fact", "note", "procedure", "state", "reference"}

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
        items = [x for x in items if x.get("scope_type") == scope_type]
    if scope_id:
        items = [x for x in items if x.get("scope_id") == scope_id]
    return sorted(items, key=lambda x: (not bool(x.get("pinned")), -int(x.get("priority", 0)), x.get("title", "")))

def create_context_item(data: dict[str, Any]) -> dict[str, Any]:
    scope_type=str(data.get("scope_type") or "").strip().lower()
    scope_id=str(data.get("scope_id") or "").strip()
    kind=str(data.get("kind") or "knowledge").strip().lower()
    title=str(data.get("title") or "").strip()
    content=str(data.get("content") or "").strip()
    if scope_type not in VALID_SCOPES: raise ValueError("Escopo de contexto inválido.")
    if not scope_id or not title or not content: raise ValueError("Escopo, título e conteúdo são obrigatórios.")
    if kind not in VALID_KINDS: raise ValueError("Tipo de contexto inválido.")
    now=_now()
    item={"id":uuid4().hex,"scope_type":scope_type,"scope_id":scope_id,"kind":kind,"title":title[:160],"content":content,"pinned":bool(data.get("pinned",False)),"priority":max(0,min(100,int(data.get("priority",50)))),"source":str(data.get("source") or "manual").strip()[:120],"active":bool(data.get("active",True)),"created_at":now,"updated_at":now}
    items=_read(CONTEXT_ITEMS_FILE)
    items.append(item)
    _write(CONTEXT_ITEMS_FILE,items)
    return item

def delete_context_item(item_id: str) -> bool:
    items=_read(CONTEXT_ITEMS_FILE)
    kept=[x for x in items if x.get("id")!=item_id]
    if len(kept)==len(items): return False
    _write(CONTEXT_ITEMS_FILE,kept)
    return True

def list_projects(company_id: str | None = None) -> list[dict[str, Any]]:
    projects=_read(PROJECTS_FILE)
    if company_id: projects=[p for p in projects if p.get("company_id")==company_id]
    return sorted(projects,key=lambda p:p.get("name", ""))

def create_project(data: dict[str, Any]) -> dict[str, Any]:
    name=str(data.get("name") or "").strip(); company_id=str(data.get("company_id") or "").strip()
    if not name or not company_id: raise ValueError("Projeto precisa de nome e empresa.")
    now=_now(); project={"id":uuid4().hex,"name":name[:160],"company_id":company_id,"status":str(data.get("status") or "active"),"created_at":now,"updated_at":now}
    projects=_read(PROJECTS_FILE); projects.append(project); _write(PROJECTS_FILE,projects); return project

def list_agent_bindings(agent_id: str | None = None) -> list[dict[str, Any]]:
    bindings=_read(BINDINGS_FILE)
    return [b for b in bindings if b.get("agent_id")==agent_id] if agent_id else bindings

def set_agent_project_bindings(agent_id: str, project_ids: list[str]) -> list[dict[str, Any]]:
    valid={p["id"] for p in list_projects()}; clean=[str(x) for x in project_ids if str(x) in valid]
    bindings=[b for b in _read(BINDINGS_FILE) if not (b.get("agent_id")==agent_id and b.get("scope_type")=="project")]
    now=_now()
    for project_id in dict.fromkeys(clean): bindings.append({"id":uuid4().hex,"agent_id":agent_id,"scope_type":"project","scope_id":project_id,"permission":"read","created_at":now})
    _write(BINDINGS_FILE,bindings); return list_agent_bindings(agent_id)

def _terms(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-ZÀ-ÿ0-9_-]{3,}",text.lower())}

def resolve_agent_context(agent: dict[str, Any], prompt: str, max_retrieved: int = 8) -> dict[str, Any]:
    scopes=[]
    if agent.get("company_id"): scopes.append(("company",str(agent["company_id"])))
    if agent.get("sector_id"): scopes.append(("sector",str(agent["sector_id"])))
    for binding in list_agent_bindings(str(agent.get("id") or "")):
        if binding.get("scope_type")=="project": scopes.append(("project",str(binding.get("scope_id") or "")))
    scopes.append(("agent",str(agent.get("id") or "")))
    scope_set=set(scopes)
    candidates=[x for x in list_context_items() if x.get("active",True) and (x.get("scope_type"),x.get("scope_id")) in scope_set]
    pinned=[x for x in candidates if x.get("pinned")]
    query=_terms(prompt)
    scored=[]
    for item in candidates:
        if item.get("pinned"): continue
        hay=_terms(str(item.get("title") or "")+" "+str(item.get("content") or ""))
        overlap=len(query & hay)
        if overlap==0 and int(item.get("priority",50))<90: continue
        scored.append((overlap*10+int(item.get("priority",50)),item))
    scored.sort(key=lambda x:x[0],reverse=True)
    retrieved=[item for score,item in scored[:max_retrieved] if score>0]
    return {"scopes":[{"type":a,"id":b} for a,b in scopes],"pinned":pinned,"retrieved":retrieved}

def context_instructions(package: dict[str, Any]) -> str:
    items=list(package.get("pinned") or [])+list(package.get("retrieved") or [])
    if not items: return ""
    lines=["\n\nCONTEXTO ORGANIZACIONAL HERDADO DO ACTIS GEN:"]
    for item in items:
        lines.append(f"- [{item.get('scope_type')}/{item.get('kind')}] {item.get('title')}: {item.get('content')}")
    lines.append("Use este contexto quando for relevante. Regras mais específicas do run/conversa têm precedência sobre contexto organizacional genérico.")
    return "\n".join(lines)
