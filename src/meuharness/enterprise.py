from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

FOUNDATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    color TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_organizations_status ON organizations(status);

CREATE TABLE IF NOT EXISTS business_units (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, code),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_business_units_organization
ON business_units(organization_id, status);

CREATE TABLE IF NOT EXISTS departments (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_unit_id TEXT,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, code),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(business_unit_id) REFERENCES business_units(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_departments_organization
ON departments(organization_id, business_unit_id, status);

CREATE TABLE IF NOT EXISTS enterprise_workspaces (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_unit_id TEXT,
    department_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'operational',
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, name, kind),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(business_unit_id) REFERENCES business_units(id) ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_enterprise_workspaces_organization
ON enterprise_workspaces(organization_id, business_unit_id, department_id, status);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, code),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_roles_organization ON roles(organization_id, status);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, user_id),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_memberships_organization_user
ON memberships(organization_id, user_id, status);

CREATE TABLE IF NOT EXISTS permissions (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_permissions_status ON permissions(status);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL,
    permission_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '*',
    condition_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(role_id, permission_id, scope),
    FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
    FOREIGN KEY(permission_id) REFERENCES permissions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_role_permissions_permission
ON role_permissions(permission_id);
"""

_VALID_STATUSES = {"active", "inactive"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _slug(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def _status(value: Any, default: str = "active") -> str:
    result = str(value or default).strip().lower()
    if result not in _VALID_STATUSES:
        raise ValueError("status deve ser active ou inactive.")
    return result


def _metadata(value: Any) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise TypeError("metadata precisa ser um objeto.")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_metadata(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row["metadata"] or "{}")
    except (json.JSONDecodeError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _id(value: Any) -> str:
    result = _clean(value, 120)
    if not result:
        raise ValueError("ID é obrigatório.")
    return result


def _connect() -> sqlite3.Connection:
    from meuharness.storage import connect

    return connect()


def init_enterprise_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(FOUNDATION_SCHEMA)


def _organization_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _decode_metadata(result)
    return result


def _unit_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _decode_metadata(result)
    return result


def _user_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _decode_metadata(result)
    return result


def _role_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["is_system"] = bool(result.get("is_system"))
    result["metadata"] = _decode_metadata(result)
    return result


def _permission_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _decode_metadata(result)
    return result


def _require_organization(conn: sqlite3.Connection, organization_id: str) -> None:
    if not conn.execute("SELECT 1 FROM organizations WHERE id=?", (organization_id,)).fetchone():
        raise ValueError("Organization não encontrada.")


def _require_business_unit(conn: sqlite3.Connection, organization_id: str, business_unit_id: str | None) -> None:
    if not business_unit_id:
        return
    row = conn.execute(
        "SELECT 1 FROM business_units WHERE id=? AND organization_id=?",
        (business_unit_id, organization_id),
    ).fetchone()
    if not row:
        raise ValueError("BusinessUnit não pertence à organization.")


def _require_department(conn: sqlite3.Connection, organization_id: str, department_id: str | None) -> None:
    if not department_id:
        return
    row = conn.execute(
        "SELECT 1 FROM departments WHERE id=? AND organization_id=?",
        (department_id, organization_id),
    ).fetchone()
    if not row:
        raise ValueError("Department não pertence à organization.")


def list_organizations() -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM organizations ORDER BY name COLLATE NOCASE").fetchall()
    return [_organization_dict(row) for row in rows]


def get_organization(organization_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM organizations WHERE id=?", (_id(organization_id),)).fetchone()
    return _organization_dict(row) if row else None


def create_organization(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    name = _clean(data.get("name"), 160)
    if not name:
        raise ValueError("Nome da organization é obrigatório.")
    slug = _slug(data.get("slug") or name)
    if not slug:
        raise ValueError("Slug da organization é obrigatório.")
    color = _clean(data.get("color"), 16) or None
    organization_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        existing = conn.execute("SELECT 1 FROM organizations WHERE slug=?", (slug,)).fetchone()
        if existing:
            slug = f"{slug}-{uuid4().hex[:6]}"
        conn.execute(
            "INSERT INTO organizations(id,name,slug,color,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (organization_id, name, slug, color, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone()
    return _organization_dict(row)


def update_organization(organization_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(organization_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone()
        if not current:
            raise ValueError("Organization não encontrada.")
        name = _clean(data.get("name", current["name"]), 160)
        if not name:
            raise ValueError("Nome da organization é obrigatório.")
        slug = _slug(data.get("slug", current["slug"]))
        if not slug:
            raise ValueError("Slug da organization é obrigatório.")
        duplicate = conn.execute("SELECT 1 FROM organizations WHERE slug=? AND id<>?", (slug, organization_id)).fetchone()
        if duplicate:
            raise ValueError("Slug da organization já existe.")
        color = _clean(data.get("color", current["color"]), 16) or None
        conn.execute(
            "UPDATE organizations SET name=?,slug=?,color=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (name, slug, color, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), organization_id),
        )
        row = conn.execute("SELECT * FROM organizations WHERE id=?", (organization_id,)).fetchone()
    return _organization_dict(row)


def delete_organization(organization_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM organizations WHERE id=?", (_id(organization_id),))
    return bool(cursor.rowcount)


def list_business_units(organization_id: str | None = None) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    query = "SELECT * FROM business_units"
    params: tuple[Any, ...] = ()
    if organization_id:
        query += " WHERE organization_id=?"
        params = (_id(organization_id),)
    query += " ORDER BY name COLLATE NOCASE"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_unit_dict(row) for row in rows]


def get_business_unit(business_unit_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM business_units WHERE id=?", (_id(business_unit_id),)).fetchone()
    return _unit_dict(row) if row else None


def create_business_unit(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(data.get("organization_id"))
    name = _clean(data.get("name"), 160)
    if not name:
        raise ValueError("Nome da business unit é obrigatório.")
    code = _slug(data.get("code") or name) or uuid4().hex[:10]
    business_unit_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        _require_organization(conn, organization_id)
        base_code = code
        suffix = 0
        while conn.execute("SELECT 1 FROM business_units WHERE organization_id=? AND code=?", (organization_id, code)).fetchone():
            suffix += 1
            code = f"{base_code}-{suffix}"
        conn.execute(
            "INSERT INTO business_units(id,organization_id,name,code,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (business_unit_id, organization_id, name, code, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM business_units WHERE id=?", (business_unit_id,)).fetchone()
    return _unit_dict(row)


def update_business_unit(business_unit_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    business_unit_id = _id(business_unit_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM business_units WHERE id=?", (business_unit_id,)).fetchone()
        if not current:
            raise ValueError("BusinessUnit não encontrada.")
        name = _clean(data.get("name", current["name"]), 160)
        if not name:
            raise ValueError("Nome da business unit é obrigatório.")
        code = _slug(data.get("code", current["code"])) or current["code"]
        duplicate = conn.execute("SELECT 1 FROM business_units WHERE organization_id=? AND code=? AND id<>?", (current["organization_id"], code, business_unit_id)).fetchone()
        if duplicate:
            raise ValueError("Code da business unit já existe na organization.")
        conn.execute(
            "UPDATE business_units SET name=?,code=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (name, code, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), business_unit_id),
        )
        row = conn.execute("SELECT * FROM business_units WHERE id=?", (business_unit_id,)).fetchone()
    return _unit_dict(row)


def delete_business_unit(business_unit_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM business_units WHERE id=?", (_id(business_unit_id),))
    return bool(cursor.rowcount)


def list_departments(organization_id: str | None = None, business_unit_id: str | None = None) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if organization_id:
        clauses.append("organization_id=?")
        params.append(_id(organization_id))
    if business_unit_id:
        clauses.append("business_unit_id=?")
        params.append(_id(business_unit_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM departments{where} ORDER BY name COLLATE NOCASE", params).fetchall()
    return [_unit_dict(row) for row in rows]


def get_department(department_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM departments WHERE id=?", (_id(department_id),)).fetchone()
    return _unit_dict(row) if row else None


def create_department(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(data.get("organization_id"))
    business_unit_id = _clean(data.get("business_unit_id"), 120) or None
    name = _clean(data.get("name"), 160)
    if not name:
        raise ValueError("Nome do department é obrigatório.")
    code = _slug(data.get("code") or name) or uuid4().hex[:10]
    department_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        _require_organization(conn, organization_id)
        _require_business_unit(conn, organization_id, business_unit_id)
        base_code = code
        suffix = 0
        while conn.execute("SELECT 1 FROM departments WHERE organization_id=? AND code=?", (organization_id, code)).fetchone():
            suffix += 1
            code = f"{base_code}-{suffix}"
        conn.execute(
            "INSERT INTO departments(id,organization_id,business_unit_id,name,code,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (department_id, organization_id, business_unit_id, name, code, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM departments WHERE id=?", (department_id,)).fetchone()
    return _unit_dict(row)


def update_department(department_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    department_id = _id(department_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM departments WHERE id=?", (department_id,)).fetchone()
        if not current:
            raise ValueError("Department não encontrado.")
        organization_id = current["organization_id"]
        business_unit_id = _clean(data.get("business_unit_id", current["business_unit_id"]), 120) or None
        _require_business_unit(conn, organization_id, business_unit_id)
        name = _clean(data.get("name", current["name"]), 160)
        if not name:
            raise ValueError("Nome do department é obrigatório.")
        code = _slug(data.get("code", current["code"])) or current["code"]
        duplicate = conn.execute("SELECT 1 FROM departments WHERE organization_id=? AND code=? AND id<>?", (organization_id, code, department_id)).fetchone()
        if duplicate:
            raise ValueError("Code do department já existe na organization.")
        conn.execute(
            "UPDATE departments SET organization_id=?,business_unit_id=?,name=?,code=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (organization_id, business_unit_id, name, code, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), department_id),
        )
        row = conn.execute("SELECT * FROM departments WHERE id=?", (department_id,)).fetchone()
    return _unit_dict(row)


def delete_department(department_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM departments WHERE id=?", (_id(department_id),))
    return bool(cursor.rowcount)


def list_workspaces(organization_id: str | None = None, business_unit_id: str | None = None, department_id: str | None = None) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (("organization_id", organization_id), ("business_unit_id", business_unit_id), ("department_id", department_id)):
        if value:
            clauses.append(f"{column}=?")
            params.append(_id(value))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM enterprise_workspaces{where} ORDER BY name COLLATE NOCASE", params).fetchall()
    return [_unit_dict(row) for row in rows]


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM enterprise_workspaces WHERE id=?", (_id(workspace_id),)).fetchone()
    return _unit_dict(row) if row else None


def create_workspace(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(data.get("organization_id"))
    business_unit_id = _clean(data.get("business_unit_id"), 120) or None
    department_id = _clean(data.get("department_id"), 120) or None
    name = _clean(data.get("name"), 160)
    if not name:
        raise ValueError("Nome do workspace é obrigatório.")
    kind = _slug(data.get("kind") or "operational") or "operational"
    workspace_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        _require_organization(conn, organization_id)
        _require_business_unit(conn, organization_id, business_unit_id)
        _require_department(conn, organization_id, department_id)
        if business_unit_id and department_id:
            department = conn.execute("SELECT business_unit_id FROM departments WHERE id=?", (department_id,)).fetchone()
            if department and department["business_unit_id"] != business_unit_id:
                raise ValueError("Department não pertence à business unit selecionada.")
        base_name = name
        suffix = 0
        while conn.execute("SELECT 1 FROM enterprise_workspaces WHERE organization_id=? AND name=? AND kind=?", (organization_id, name, kind)).fetchone():
            suffix += 1
            name = f"{base_name}-{suffix}"
        conn.execute(
            "INSERT INTO enterprise_workspaces(id,organization_id,business_unit_id,department_id,name,kind,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (workspace_id, organization_id, business_unit_id, department_id, name, kind, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM enterprise_workspaces WHERE id=?", (workspace_id,)).fetchone()
    return _unit_dict(row)


def update_workspace(workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    workspace_id = _id(workspace_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM enterprise_workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not current:
            raise ValueError("Workspace não encontrado.")
        organization_id = current["organization_id"]
        business_unit_id = _clean(data.get("business_unit_id", current["business_unit_id"]), 120) or None
        department_id = _clean(data.get("department_id", current["department_id"]), 120) or None
        _require_business_unit(conn, organization_id, business_unit_id)
        _require_department(conn, organization_id, department_id)
        if business_unit_id and department_id:
            department = conn.execute("SELECT business_unit_id FROM departments WHERE id=?", (department_id,)).fetchone()
            if department and department["business_unit_id"] != business_unit_id:
                raise ValueError("Department não pertence à business unit selecionada.")
        name = _clean(data.get("name", current["name"]), 160)
        if not name:
            raise ValueError("Nome do workspace é obrigatório.")
        kind = _slug(data.get("kind", current["kind"])) or current["kind"]
        duplicate = conn.execute("SELECT 1 FROM enterprise_workspaces WHERE organization_id=? AND name=? AND kind=? AND id<>?", (organization_id, name, kind, workspace_id)).fetchone()
        if duplicate:
            raise ValueError("Workspace já existe na organization.")
        conn.execute(
            "UPDATE enterprise_workspaces SET organization_id=?,business_unit_id=?,department_id=?,name=?,kind=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (organization_id, business_unit_id, department_id, name, kind, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), workspace_id),
        )
        row = conn.execute("SELECT * FROM enterprise_workspaces WHERE id=?", (workspace_id,)).fetchone()
    return _unit_dict(row)


def delete_workspace(workspace_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM enterprise_workspaces WHERE id=?", (_id(workspace_id),))
    return bool(cursor.rowcount)


def list_users() -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY display_name COLLATE NOCASE").fetchall()
    return [_user_dict(row) for row in rows]


def get_user(user_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (_id(user_id),)).fetchone()
    return _user_dict(row) if row else None


def create_user(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    email = _clean(data.get("email"), 320).lower()
    display_name = _clean(data.get("display_name") or data.get("name"), 160)
    if not email or "@" not in email:
        raise ValueError("Email válido é obrigatório.")
    if not display_name:
        raise ValueError("display_name é obrigatório.")
    user_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users(id,email,display_name,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, email, display_name, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _user_dict(row)


def update_user(user_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    user_id = _id(user_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not current:
            raise ValueError("User não encontrado.")
        email = _clean(data.get("email", current["email"]), 320).lower()
        display_name = _clean(data.get("display_name", current["display_name"]), 160)
        if not email or "@" not in email or not display_name:
            raise ValueError("email e display_name válidos são obrigatórios.")
        conn.execute(
            "UPDATE users SET email=?,display_name=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (email, display_name, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _user_dict(row)


def delete_user(user_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM users WHERE id=?", (_id(user_id),))
    return bool(cursor.rowcount)


def list_memberships(organization_id: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if organization_id:
        clauses.append("organization_id=?")
        params.append(_id(organization_id))
    if user_id:
        clauses.append("user_id=?")
        params.append(_id(user_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM memberships{where} ORDER BY created_at DESC", params).fetchall()
    return [_decode_membership(row) for row in rows]


def get_membership(membership_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM memberships WHERE id=?", (_id(membership_id),)).fetchone()
    return _decode_membership(row) if row else None


def _decode_membership(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _decode_metadata(result)
    return result


def create_membership(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(data.get("organization_id"))
    user_id = _id(data.get("user_id"))
    role_id = _clean(data.get("role_id"), 120) or None
    membership_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        _require_organization(conn, organization_id)
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ValueError("User não encontrado.")
        if role_id:
            role = conn.execute("SELECT 1 FROM roles WHERE id=? AND organization_id=?", (role_id, organization_id)).fetchone()
            if not role:
                raise ValueError("Role não pertence à organization.")
        conn.execute(
            "INSERT INTO memberships(id,organization_id,user_id,role_id,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (membership_id, organization_id, user_id, role_id, _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM memberships WHERE id=?", (membership_id,)).fetchone()
    return _decode_membership(row)


def update_membership(membership_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    membership_id = _id(membership_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM memberships WHERE id=?", (membership_id,)).fetchone()
        if not current:
            raise ValueError("Membership não encontrado.")
        organization_id = current["organization_id"]
        role_id = _clean(data.get("role_id", current["role_id"]), 120) or None
        if role_id:
            role = conn.execute("SELECT 1 FROM roles WHERE id=? AND organization_id=?", (role_id, organization_id)).fetchone()
            if not role:
                raise ValueError("Role não pertence à organization.")
        conn.execute(
            "UPDATE memberships SET role_id=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (role_id, _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), membership_id),
        )
        row = conn.execute("SELECT * FROM memberships WHERE id=?", (membership_id,)).fetchone()
    return _decode_membership(row)


def delete_membership(membership_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM memberships WHERE id=?", (_id(membership_id),))
    return bool(cursor.rowcount)


def list_roles(organization_id: str | None = None) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    query = "SELECT * FROM roles"
    params: tuple[Any, ...] = ()
    if organization_id:
        query += " WHERE organization_id=?"
        params = (_id(organization_id),)
    query += " ORDER BY name COLLATE NOCASE"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_role_dict(row) for row in rows]


def get_role(role_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM roles WHERE id=?", (_id(role_id),)).fetchone()
    return _role_dict(row) if row else None


def create_role(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    organization_id = _id(data.get("organization_id"))
    name = _clean(data.get("name"), 160)
    if not name:
        raise ValueError("Nome da role é obrigatório.")
    code = _slug(data.get("code") or name) or uuid4().hex[:10]
    role_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        _require_organization(conn, organization_id)
        base_code = code
        suffix = 0
        while conn.execute("SELECT 1 FROM roles WHERE organization_id=? AND code=?", (organization_id, code)).fetchone():
            suffix += 1
            code = f"{base_code}-{suffix}"
        conn.execute(
            "INSERT INTO roles(id,organization_id,name,code,description,is_system,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (role_id, organization_id, name, code, _clean(data.get("description"), 500), int(bool(data.get("is_system"))), _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    return _role_dict(row)


def update_role(role_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    role_id = _id(role_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not current:
            raise ValueError("Role não encontrada.")
        organization_id = current["organization_id"]
        name = _clean(data.get("name", current["name"]), 160)
        if not name:
            raise ValueError("Nome da role é obrigatório.")
        code = _slug(data.get("code", current["code"])) or current["code"]
        duplicate = conn.execute("SELECT 1 FROM roles WHERE organization_id=? AND code=? AND id<>?", (organization_id, code, role_id)).fetchone()
        if duplicate:
            raise ValueError("Code da role já existe na organization.")
        conn.execute(
            "UPDATE roles SET name=?,code=?,description=?,is_system=?,status=?,metadata=?,updated_at=? WHERE organization_id=? AND id=?",
            (name, code, _clean(data.get("description", current["description"]), 500), int(bool(data.get("is_system", current["is_system"]))), _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), organization_id, role_id),
        )
        row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    return _role_dict(row)


def delete_role(role_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM roles WHERE id=?", (_id(role_id),))
    return bool(cursor.rowcount)


def list_permissions() -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM permissions ORDER BY code COLLATE NOCASE").fetchall()
    return [_permission_dict(row) for row in rows]


def get_permission(permission_id: str) -> dict[str, Any] | None:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM permissions WHERE id=?", (_id(permission_id),)).fetchone()
    return _permission_dict(row) if row else None


def create_permission(data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    code = _slug(data.get("code") or data.get("name"))
    if not code:
        raise ValueError("Code da permission é obrigatório.")
    permission_id = _id(data.get("id")) if data.get("id") else uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO permissions(id,code,description,status,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (permission_id, code, _clean(data.get("description") or data.get("name"), 500), _status(data.get("status")), _metadata(data.get("metadata")), now, now),
        )
        row = conn.execute("SELECT * FROM permissions WHERE id=?", (permission_id,)).fetchone()
    return _permission_dict(row)


def update_permission(permission_id: str, data: dict[str, Any]) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    permission_id = _id(permission_id)
    with _connect() as conn:
        current = conn.execute("SELECT * FROM permissions WHERE id=?", (permission_id,)).fetchone()
        if not current:
            raise ValueError("Permission não encontrada.")
        code = _slug(data.get("code", current["code"]))
        if not code:
            raise ValueError("Code da permission é obrigatório.")
        conn.execute(
            "UPDATE permissions SET code=?,description=?,status=?,metadata=?,updated_at=? WHERE id=?",
            (code, _clean(data.get("description", current["description"]), 500), _status(data.get("status", current["status"])), _metadata(data.get("metadata", _decode_metadata(current))), _now(), permission_id),
        )
        row = conn.execute("SELECT * FROM permissions WHERE id=?", (permission_id,)).fetchone()
    return _permission_dict(row)


def delete_permission(permission_id: str) -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM permissions WHERE id=?", (_id(permission_id),))
    return bool(cursor.rowcount)


def grant_permission(role_id: str, permission_id: str, *, scope: str = "*", condition: dict[str, Any] | None = None) -> dict[str, Any]:
    from meuharness.storage import init_db

    init_db()
    role_id = _id(role_id)
    permission_id = _id(permission_id)
    scope = _clean(scope, 160) or "*"
    condition_json = _metadata(condition) if condition is not None else None
    with _connect() as conn:
        if not conn.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
            raise ValueError("Role não encontrada.")
        if not conn.execute("SELECT 1 FROM permissions WHERE id=?", (permission_id,)).fetchone():
            raise ValueError("Permission não encontrada.")
        conn.execute(
            "INSERT INTO role_permissions(role_id,permission_id,scope,condition_json,created_at) VALUES(?,?,?,?,?) ON CONFLICT(role_id,permission_id,scope) DO UPDATE SET condition_json=excluded.condition_json",
            (role_id, permission_id, scope, condition_json, _now()),
        )
        row = conn.execute("SELECT * FROM role_permissions WHERE role_id=? AND permission_id=? AND scope=?", (role_id, permission_id, scope)).fetchone()
    return dict(row)


def revoke_permission(role_id: str, permission_id: str, *, scope: str = "*") -> bool:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM role_permissions WHERE role_id=? AND permission_id=? AND scope=?", (_id(role_id), _id(permission_id), _clean(scope, 160) or "*"))
    return bool(cursor.rowcount)


def permissions_for_role(role_id: str) -> list[dict[str, Any]]:
    from meuharness.storage import init_db

    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.*, rp.scope, rp.condition_json, rp.created_at AS granted_at FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE rp.role_id=? ORDER BY p.code COLLATE NOCASE",
            (_id(role_id),),
        ).fetchall()
    result = []
    for row in rows:
        item = _permission_dict(row)
        item["scope"] = row["scope"]
        item["condition"] = None
        if row["condition_json"]:
            try:
                item["condition"] = json.loads(row["condition_json"])
            except json.JSONDecodeError:
                item["condition"] = {}
        item["granted_at"] = row["granted_at"]
        result.append(item)
    return result
