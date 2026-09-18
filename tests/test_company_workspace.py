from datetime import UTC, datetime, timedelta

import pytest

from meuharness import storage
from meuharness.agents import create_agent, create_company
from meuharness.company_bus import create_company_task
from meuharness.company_workspace import (
    agent_workspace_snapshot,
    company_workspace_snapshot,
    create_work_item,
    get_work_item,
    list_work_items,
    update_work_item,
)
from meuharness.control_tools import build_general_tools
from meuharness.feature_registry import feature_ids


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def _agent(name, company_id, capabilities=None):
    return create_agent(
        {
            "name": name,
            "company_id": company_id,
            "model": "test/model",
            "instructions": "test",
            "company_capabilities": capabilities or [],
        }
    )


def test_workspace_isolated_by_company_and_agent():
    acme = create_company({"name": "ACME"})
    other = create_company({"name": "OTHER"})
    alice = _agent("Alice", acme["id"])
    bob = _agent("Bob", acme["id"])
    outsider = _agent("Outsider", other["id"])

    own = create_work_item(
        company_id=acme["id"],
        owner_type="agent",
        owner_id=alice["id"],
        title="Responder cliente",
    )
    create_work_item(
        company_id=other["id"],
        owner_type="agent",
        owner_id=outsider["id"],
        title="Outro trabalho",
    )

    assert [x["id"] for x in list_work_items(acme["id"])] == [own["id"]]
    assert [x["id"] for x in agent_workspace_snapshot(alice["id"])["items"]] == [own["id"]]
    assert agent_workspace_snapshot(bob["id"])["items"] == []

    with pytest.raises(PermissionError):
        update_work_item(
            own["id"],
            expected_company_id=other["id"],
            changes={"status": "completed"},
        )
    with pytest.raises(PermissionError):
        update_work_item(
            own["id"],
            expected_company_id=acme["id"],
            expected_owner_type="agent",
            expected_owner_id=bob["id"],
            changes={"status": "completed"},
        )


def test_operator_queue_and_overdue_snapshot():
    company = create_company({"name": "ACME"})
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    item = create_work_item(
        company_id=company["id"],
        owner_type="operator",
        owner_id="operator",
        title="Aprovar exceção",
        priority="critical",
        due_at=past,
        next_action="Decidir",
    )

    snapshot = company_workspace_snapshot(company["id"])
    assert snapshot["operator"]["count"] == 1
    assert snapshot["operator"]["overdue"] == 1
    assert snapshot["summary"]["overdue"] == 1
    assert snapshot["summary"]["priority"]["critical"] == 1
    assert get_work_item(item["id"])["overdue"] is True


def test_agent_can_update_only_own_workspace_via_native_tools():
    company = create_company({"name": "ACME"})
    alice = _agent("Alice", company["id"])
    bob = _agent("Bob", company["id"])
    own = create_work_item(
        company_id=company["id"],
        owner_type="agent",
        owner_id=alice["id"],
        title="Preparar retorno",
    )
    foreign = create_work_item(
        company_id=company["id"],
        owner_type="agent",
        owner_id=bob["id"],
        title="Comprar material",
    )

    from meuharness.company_workspace import build_company_workspace_tools

    tools = {tool.__name__: tool for tool in build_company_workspace_tools(alice["id"])}
    result = tools["company_workspace_update"](own["id"], status="in_progress")
    assert '"status": "in_progress"' in result

    with pytest.raises(PermissionError):
        tools["company_workspace_update"](foreign["id"], status="completed")


def test_company_task_creates_linked_work_item_for_target_or_operator():
    company = create_company({"name": "ACME"})
    source = _agent("Comercial", company["id"], ["customer-intake"])
    target = _agent("Orçamento", company["id"], ["quote"])

    assigned = create_company_task(
        company_id=company["id"],
        source_agent_id=source["id"],
        kind="quote",
        objective="Revisar orçamento 513",
    )
    assigned_item = get_work_item(assigned["workspace_item_id"])
    assert assigned_item["owner_type"] == "agent"
    assert assigned_item["owner_id"] == target["id"]
    assert assigned_item["refs"]["company_task"] == assigned["id"]

    queued = create_company_task(
        company_id=company["id"],
        source_agent_id=source["id"],
        kind="unknown-capability",
        objective="Resolver exceção",
    )
    queued_item = get_work_item(queued["workspace_item_id"])
    assert queued_item["owner_type"] == "operator"
    assert queued_item["status"] == "inbox"


def test_workspace_reconciles_legacy_company_tasks():
    company = create_company({"name": "ACME"})
    source = _agent("Comercial", company["id"], ["customer-intake"])
    target = _agent("Orçamento", company["id"], ["quote"])
    now = datetime.now(UTC).isoformat()
    storage.save_collection(
        "company_tasks",
        [
            {
                "id": "legacy-task",
                "company_id": company["id"],
                "kind": "quote",
                "objective": "Orçamento legado",
                "payload": {},
                "source_agent_id": source["id"],
                "assigned_agent_id": target["id"],
                "status": "completed",
                "result": {"text": "ok"},
                "run_id": "legacy-run",
                "error": None,
                "created_at": now,
                "updated_at": now,
                "finished_at": now,
            }
        ],
    )

    snapshot = company_workspace_snapshot(company["id"])
    items = list_work_items(company["id"])
    persisted_task = storage.load_collection("company_tasks")[0]

    assert snapshot["summary"]["total"] == 1
    assert len(items) == 1
    assert items[0]["status"] == "completed"
    assert items[0]["owner_id"] == target["id"]
    assert items[0]["refs"]["company_task"] == "legacy-task"
    assert persisted_task["workspace_item_id"] == items[0]["id"]


def test_general_discovers_and_administers_company_workspace():
    assert "company-workspace" in feature_ids()
    tools = {tool.__name__: tool for tool in build_general_tools()}
    expected = {
        "actis_get_company_workspace",
        "actis_list_company_work_items",
        "actis_create_company_work_item",
        "actis_update_company_work_item",
    }
    assert expected <= set(tools)

    company = create_company({"name": "ACME"})
    agent = _agent("Financeiro", company["id"])
    raw = tools["actis_create_company_work_item"](
        company["id"],
        "Conferir recebíveis",
        owner_type="agent",
        owner_id=agent["id"],
    )
    assert "Conferir recebíveis" in raw
    snapshot = tools["actis_get_company_workspace"](company["id"])
    assert agent["id"] in snapshot
