from __future__ import annotations

import sqlite3

import pytest

from meuharness import storage
from meuharness.enterprise import (
    create_business_unit,
    create_department,
    create_membership,
    create_organization,
    create_permission,
    create_role,
    create_user,
    create_workspace,
    get_organization,
    grant_permission,
    list_business_units,
    list_departments,
    list_memberships,
    list_permissions,
    list_roles,
    list_workspaces,
    permissions_for_role,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def _organization(name: str = "Acme"):
    return create_organization({"name": name})


def test_foundation_schema_is_idempotent():
    storage.init_db()
    storage.init_db()

    with storage.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    expected = {
        "organizations",
        "business_units",
        "departments",
        "enterprise_workspaces",
        "users",
        "memberships",
        "roles",
        "permissions",
        "role_permissions",
    }
    assert expected <= tables


def test_organization_hierarchy_and_workspace_crud():
    organization = _organization()
    unit = create_business_unit({"organization_id": organization["id"], "name": "Operations"})
    department = create_department(
        {
            "organization_id": organization["id"],
            "business_unit_id": unit["id"],
            "name": "Customer Success",
        }
    )
    workspace = create_workspace(
        {
            "organization_id": organization["id"],
            "business_unit_id": unit["id"],
            "department_id": department["id"],
            "name": "Atendimento",
            "kind": "support",
        }
    )

    assert get_organization(organization["id"])["slug"].startswith("acme")
    assert [item["id"] for item in list_business_units(organization["id"])] == [unit["id"]]
    assert [item["id"] for item in list_departments(organization["id"])] == [department["id"]]
    assert [item["id"] for item in list_workspaces(organization["id"])] == [workspace["id"]]
    assert workspace["department_id"] == department["id"]

    updated = create_workspace(
        {
            "organization_id": organization["id"],
            "business_unit_id": unit["id"],
            "department_id": department["id"],
            "name": "Atendimento",
            "kind": "support",
        }
    )
    assert updated["id"] != workspace["id"]
    assert updated["name"] == "Atendimento-1"


def test_user_membership_role_and_permission():
    organization = _organization()
    user = create_user({"email": "alice@example.com", "display_name": "Alice"})
    role = create_role({"organization_id": organization["id"], "name": "Operator", "code": "operator"})
    permission = create_permission({"code": "workspace.read", "description": "Read workspaces"})
    grant = grant_permission(role["id"], permission["id"], scope="organization", condition={"team": "ops"})
    membership = create_membership(
        {
            "organization_id": organization["id"],
            "user_id": user["id"],
            "role_id": role["id"],
        }
    )

    assert grant["scope"] == "organization"
    assert membership["role_id"] == role["id"]
    assert [item["id"] for item in list_memberships(organization["id"])] == [membership["id"]]
    assert [item["id"] for item in list_roles(organization["id"])] == [role["id"]]
    assert [item["id"] for item in list_permissions()] == [permission["id"]]
    assert permissions_for_role(role["id"])[0]["condition"] == {"team": "ops"}


def test_cross_organization_relations_are_rejected():
    first = _organization("First")
    second = _organization("Second")
    first_role = create_role({"organization_id": first["id"], "name": "Admin", "code": "admin"})
    user = create_user({"email": "bob@example.com", "display_name": "Bob"})

    with pytest.raises(ValueError, match="Role não pertence"):
        create_membership({"organization_id": second["id"], "user_id": user["id"], "role_id": first_role["id"]})

    first_unit = create_business_unit({"organization_id": first["id"], "name": "First Unit"})
    with pytest.raises(ValueError, match="não pertence"):
        create_department({"organization_id": second["id"], "business_unit_id": first_unit["id"], "name": "Invalid"})

    with pytest.raises(sqlite3.IntegrityError):
        create_membership({"organization_id": first["id"], "user_id": user["id"]})
        create_membership({"organization_id": first["id"], "user_id": user["id"]})
