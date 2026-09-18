"""Markdown-backed long-term memory scoped to ACTIS projects."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.storage import load_collection, save_collection

MEMORY_DIRNAME = ".actis-memory"
BINDINGS_COLLECTION = "project_memory_bindings"
VALID_KINDS = {"observation", "decision", "gotcha", "workstream"}
PROMOTION_TARGETS = {"decision", "gotcha", "workstream"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    raw = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return clean[:64] or "memory"
def _workspace(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Workspace inexistente: {path}")
    return path


def memory_root(workspace_path: str | Path) -> Path:
    return _workspace(workspace_path) / MEMORY_DIRNAME


def _read_bindings() -> list[dict[str, Any]]:
    return load_collection(BINDINGS_COLLECTION)


def _write_bindings(items: list[dict[str, Any]]) -> None:
    save_collection(BINDINGS_COLLECTION, items)


def list_project_memory_bindings(project_id: str | None = None) -> list[dict[str, Any]]:
    rows = _read_bindings()
    if project_id:
        rows = [row for row in rows if str(row.get("project_id")) == str(project_id)]
    return rows


def project_memory_binding(project_id: str) -> dict[str, Any] | None:
    rows = list_project_memory_bindings(project_id)
    return rows[0] if rows else None
def init_project_memory(workspace_path: str | Path) -> dict[str, Any]:
    workspace = _workspace(workspace_path)
    root = workspace / MEMORY_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    for kind in sorted(VALID_KINDS):
        (root / kind).mkdir(exist_ok=True)
    _write_index(root)
    return {"workspace_path": str(workspace), "memory_root": str(root), "kinds": sorted(VALID_KINDS)}


def bind_project_memory(project_id: str, workspace_path: str | Path) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id é obrigatório")
    initialized = init_project_memory(workspace_path)
    now = _now()
    rows = _read_bindings()
    current = next((row for row in rows if str(row.get("project_id")) == project_id), None)
    if current:
        current["workspace_path"] = initialized["workspace_path"]
        current["updated_at"] = now
        binding = current
    else:
        binding = {"id": uuid4().hex, "project_id": project_id, "workspace_path": initialized["workspace_path"],
                   "created_at": now, "updated_at": now}
        rows.append(binding)
    _write_bindings(rows)
    return dict(binding)
def _frontmatter(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            if handle.readline().strip() != "---":
                return metadata
            for _ in range(40):
                line = handle.readline()
                if not line or line.strip() == "---":
                    break
                key, sep, raw = line.partition(":")
                if not sep:
                    continue
                value = raw.strip()
                try:
                    metadata[key.strip()] = json.loads(value)
                except json.JSONDecodeError:
                    metadata[key.strip()] = value
    except OSError:
        return {}
    return metadata


def _entry_path(root: Path, kind: str, title: str, entry_id: str) -> Path:
    return root / kind / f"{_slug(title)}-{entry_id[:8]}.md"


def _render_entry(metadata: dict[str, Any], content: str) -> str:
    keys = ("id", "kind", "title", "summary", "tags", "status", "created_at", "updated_at", "source_entry_id", "promoted_to")
    lines = ["---"]
    for key in keys:
        if key in metadata and metadata[key] not in (None, ""):
            lines.append(f"{key}: {json.dumps(metadata[key], ensure_ascii=False)}")
    lines.extend(["---", "", f"# {metadata['title']}", "", content.strip(), ""])
    return "\n".join(lines)


def catalog_project_memory(workspace_path: str | Path) -> list[dict[str, Any]]:
    root = memory_root(workspace_path)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for kind in sorted(VALID_KINDS):
        folder = root / kind
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            meta = _frontmatter(path)
            if not meta.get("id"):
                continue
            meta["relative_path"] = str(path.relative_to(root))
            rows.append(meta)
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows


def _write_index(root: Path) -> None:
    workspace = root.parent
    rows = catalog_project_memory(workspace) if root.exists() else []
    lines = ["# ACTIS Project Memory", "", "This index contains metadata only. Page bodies are loaded on demand.", ""]
    lines.extend(["| kind | title | summary | tags | status | id |", "|---|---|---|---|---|---|"])
    for row in rows:
        tags = ", ".join(str(tag) for tag in row.get("tags") or [])
        values = [
            str(row.get("kind") or ""), str(row.get("title") or ""), str(row.get("summary") or ""),
            tags, str(row.get("status") or ""), str(row.get("id") or ""),
        ]
        safe = [value.replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append("| " + " | ".join(safe) + " |")
    (root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_project_memory(
    workspace_path: str | Path, *, kind: str, title: str, summary: str,
    content: str, tags: list[str] | None = None, status: str | None = None,
    source_entry_id: str | None = None,
) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind not in VALID_KINDS:
        raise ValueError(f"kind inválido: {kind}")
    title, summary, content = str(title).strip(), str(summary).strip(), str(content).strip()
    if not title or not summary or not content:
        raise ValueError("title, summary e content são obrigatórios")
    root = Path(init_project_memory(workspace_path)["memory_root"])
    now, entry_id = _now(), uuid4().hex
    default_status = "unverified" if kind == "observation" else ("active" if kind == "workstream" else "confirmed")
    metadata: dict[str, Any] = {
        "id": entry_id, "kind": kind, "title": title[:160], "summary": summary[:500],
        "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()][:20],
        "status": str(status or default_status).strip(), "created_at": now, "updated_at": now,
    }
    if source_entry_id:
        metadata["source_entry_id"] = source_entry_id
    path = _entry_path(root, kind, title, entry_id)
    path.write_text(_render_entry(metadata, content), encoding="utf-8")
    _write_index(root)
    return {**metadata, "relative_path": str(path.relative_to(root))}


def read_project_memory(workspace_path: str | Path, entry_id: str) -> dict[str, Any] | None:
    root = memory_root(workspace_path)
    entry_id = str(entry_id or "").strip()
    row = next((item for item in catalog_project_memory(workspace_path) if str(item.get("id")) == entry_id), None)
    if not row:
        return None
    path = root / str(row["relative_path"])
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n.*?\n---\n(?:\n)?", text, flags=re.DOTALL)
    body = text[match.end():].strip() if match else text.strip()
    body = re.sub(r"^# .+?\n+", "", body, count=1).strip()
    return {**row, "content": body}
def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return {term for term in re.findall(r"[a-z0-9_-]{3,}", normalized)}


def recall_project_memory(workspace_path: str | Path, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    if not query_terms:
        return []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in catalog_project_memory(workspace_path):
        title_terms = _terms(str(row.get("title") or ""))
        summary_terms = _terms(str(row.get("summary") or ""))
        tag_terms = _terms(" ".join(str(tag) for tag in row.get("tags") or []))
        score = 8 * len(query_terms & title_terms) + 5 * len(query_terms & summary_terms) + 3 * len(query_terms & tag_terms)
        if score:
            scored.append((score, str(row.get("updated_at") or ""), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    result: list[dict[str, Any]] = []
    for _, _, row in scored[:max(1, min(int(limit), 12))]:
        full = read_project_memory(workspace_path, str(row["id"]))
        if full:
            result.append(full)
    return result


def promote_observation(
    workspace_path: str | Path, observation_id: str, *, target_kind: str, verification_note: str,
) -> dict[str, Any]:
    target_kind = str(target_kind or "").strip().lower()
    if target_kind not in PROMOTION_TARGETS:
        raise ValueError("target_kind precisa ser decision, gotcha ou workstream")
    source = read_project_memory(workspace_path, observation_id)
    if not source or source.get("kind") != "observation":
        raise ValueError("Observation não encontrada")
    verification_note = str(verification_note or "").strip()
    if not verification_note:
        raise ValueError("verification_note é obrigatório para promover uma observation")
    promoted = write_project_memory(
        workspace_path, kind=target_kind, title=str(source["title"]), summary=str(source["summary"]),
        content=str(source["content"]) + "\n\n## Verification\n" + verification_note,
        tags=list(source.get("tags") or []), source_entry_id=str(source["id"]),
    )
    root = memory_root(workspace_path)
    source_path = root / str(source["relative_path"])
    source_meta = {key: value for key, value in source.items() if key not in {"content", "relative_path"}}
    source_meta["status"], source_meta["promoted_to"], source_meta["updated_at"] = "promoted", promoted["id"], _now()
    source_path.write_text(_render_entry(source_meta, str(source["content"])), encoding="utf-8")
    _write_index(root)
    return {"source": {**source_meta, "relative_path": source["relative_path"]}, "promoted": promoted}
def project_memory_for_agent(agent_id: str, prompt: str, *, limit_per_project: int = 3) -> list[dict[str, Any]]:
    from meuharness.contexts import list_agent_bindings

    project_ids = [
        str(row.get("scope_id")) for row in list_agent_bindings(agent_id)
        if row.get("scope_type") == "project" and row.get("scope_id")
    ]
    binding_map = {str(row["project_id"]): row for row in list_project_memory_bindings() if row.get("project_id")}
    result: list[dict[str, Any]] = []
    for project_id in project_ids:
        binding = binding_map.get(project_id)
        if not binding:
            continue
        try:
            matches = recall_project_memory(str(binding["workspace_path"]), prompt, limit=limit_per_project)
        except (OSError, ValueError):
            continue
        for item in matches:
            result.append({**item, "project_id": project_id, "workspace_path": str(binding["workspace_path"])})
    return result


def project_memory_instructions(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["\n\nMEMÓRIA MARKDOWN RELEVANTE DOS PROJETOS:"]
    for item in items:
        body = str(item.get("content") or "").strip()
        lines.append(
            f"- [project={item.get('project_id')} kind={item.get('kind')} status={item.get('status')}] "
            f"{item.get('title')}: {body[:4000]}"
        )
    lines.append(
        "Estas páginas foram selecionadas pelo catálogo leve da .actis-memory; trate observations como não verificadas "
        "até promoção explícita. Se houver conflito, o código/runtime e a instrução atual têm precedência."
    )
    return "\n".join(lines)
