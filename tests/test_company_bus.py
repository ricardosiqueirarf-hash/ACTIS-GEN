import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from meuharness import storage
from meuharness.agents import create_agent, create_company
from meuharness.company_bus import create_company_task, dispatch_company_task, list_company_tasks


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def _agent(name, company_id, capabilities=None):
    return create_agent({
        "name": name, "company_id": company_id, "model": "test/model",
        "instructions": "test", "company_capabilities": capabilities or [],
    })


def test_company_task_routes_only_inside_same_company():
    acme = create_company({"name": "ACME"})
    other = create_company({"name": "OTHER"})
    source = _agent("Canal", acme["id"], ["customer-intake"])
    target = _agent("Orcador", acme["id"], ["quote"])
    _agent("Orcador externo", other["id"], ["quote"])
    task = create_company_task(company_id=acme["id"], source_agent_id=source["id"],
                               kind="quote", objective="Gerar orçamento", payload={"item": 1})
    assert task["assigned_agent_id"] == target["id"]
    assert task["company_id"] == acme["id"]
    assert len(list_company_tasks(acme["id"])) == 1
    assert list_company_tasks(other["id"]) == []


def test_company_task_stays_queued_without_capable_agent():
    acme = create_company({"name": "ACME"})
    source = _agent("Canal", acme["id"], ["customer-intake"])
    task = create_company_task(company_id=acme["id"], source_agent_id=source["id"],
                               kind="quote", objective="Gerar orçamento")
    assert task["status"] == "queued"
    assert task["assigned_agent_id"] is None


def test_dispatch_executes_target_and_persists_result(monkeypatch):
    acme = create_company({"name": "ACME"})
    source = _agent("WhatsApp", acme["id"], ["customer-intake"])
    target = _agent("Orcamento", acme["id"], ["quote"])
    task = create_company_task(company_id=acme["id"], source_agent_id=source["id"],
                               kind="quote", objective="Gerar orçamento", payload={"width": 600})
    calls = {}
    async def fake_execute(agent_id, prompt, **kwargs):
        calls.update(agent_id=agent_id, prompt=prompt, kwargs=kwargs)
        return SimpleNamespace(run_id="child-run", text="R$ 1.234,00", model="test/model")
    import meuharness.execution_service as execution_service
    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    result = asyncio.run(dispatch_company_task(task["id"]))
    assert calls["agent_id"] == target["id"]
    assert calls["kwargs"]["parent_agent_id"] == source["id"]
    assert result["status"] == "completed"
    assert result["run_id"] == "child-run"
    assert result["result"]["text"] == "R$ 1.234,00"


def test_dispatch_preserves_planned_child_status(monkeypatch):
    acme = create_company({"name": "ACME"})
    source = _agent("Canal", acme["id"], ["customer-intake"])
    _agent("Orcamento", acme["id"], ["quote"])
    task = create_company_task(company_id=acme["id"], source_agent_id=source["id"],
                               kind="quote", objective="Gerar orçamento")
    async def fake_execute(agent_id, prompt, **kwargs):
        return SimpleNamespace(run_id="planned-child", text="Vou fazer", model="test/model")
    import meuharness.agents as agents_module
    import meuharness.execution_service as execution_service
    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    monkeypatch.setattr(agents_module, "get_run", lambda run_id: {"id": run_id, "status": "planned", "error": None})
    result = asyncio.run(dispatch_company_task(task["id"]))
    assert result["status"] == "planned"
    assert result["finished_at"] is None


def test_concurrent_agent_creates_do_not_lose_updates():
    acme = create_company({"name": "ACME"})
    names = [f"Worker {index}" for index in range(8)]

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        created = list(pool.map(lambda name: _agent(name, acme["id"]), names))

    from meuharness.agents import list_agents
    persisted = {agent["id"] for agent in list_agents()}
    assert {agent["id"] for agent in created} <= persisted
    assert len(persisted) == len(names)


def test_whatsapp_delivery_company_task_blocks_without_verified_send(monkeypatch):
    acme = create_company({"name": "ACME"})
    source = _agent("Financeiro", acme["id"], ["finance"])
    _agent("WhatsApp", acme["id"], ["operator-notification"])
    task = create_company_task(
        company_id=acme["id"],
        source_agent_id=source["id"],
        kind="operator-notification",
        objective="Enviar relatório",
        payload={"contact": "85988241620", "message": "relatório"},
    )

    async def fake_execute(agent_id, prompt, **kwargs):
        return SimpleNamespace(run_id="wa-child", text="status uncertain", model="test/model")

    import meuharness.durable_runs as durable_runs
    import meuharness.execution_service as execution_service
    from meuharness.adapters.maf import observed_tool

    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    monkeypatch.setattr(observed_tool, "get_run_tool_trace", lambda run_id: [{"tool": "whatsapp_send_message", "ok": False}])
    monkeypatch.setattr(durable_runs, "list_checkpoints", lambda run_id, limit=100: [])

    result = asyncio.run(dispatch_company_task(task["id"]))
    assert result["status"] == "blocked"
    assert result["error"] == "whatsapp_delivery_not_verified"


def test_whatsapp_delivery_company_task_completes_with_verified_send(monkeypatch):
    acme = create_company({"name": "ACME"})
    source = _agent("Financeiro", acme["id"], ["finance"])
    _agent("WhatsApp", acme["id"], ["operator-notification"])
    task = create_company_task(
        company_id=acme["id"],
        source_agent_id=source["id"],
        kind="operator-notification",
        objective="Enviar relatório",
        payload={"contact": "85988241620", "message": "relatório"},
    )

    async def fake_execute(agent_id, prompt, **kwargs):
        return SimpleNamespace(run_id="wa-child-ok", text="sent verified", model="test/model")

    import meuharness.durable_runs as durable_runs
    import meuharness.execution_service as execution_service
    from meuharness.adapters.maf import observed_tool

    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    monkeypatch.setattr(observed_tool, "get_run_tool_trace", lambda run_id: [{"tool": "whatsapp_send_message", "ok": True}])
    monkeypatch.setattr(durable_runs, "list_checkpoints", lambda run_id, limit=100: [])

    result = asyncio.run(dispatch_company_task(task["id"]))
    assert result["status"] == "completed"
    assert result["error"] is None


def test_run_manager_tools_are_company_scoped_and_report_persisted():
    from meuharness.agents import cancel_run_record, start_run
    from meuharness.company_bus import (
        build_company_tools,
        company_runtime_instructions,
        list_company_run_reports,
    )

    acme = create_company({"name": "ACME"})
    other = create_company({"name": "OTHER"})
    manager = _agent("Gerente", acme["id"], ["company-management", "run-management"])
    worker = _agent("Operario", acme["id"], ["quote"])
    outsider = _agent("Externo", other["id"], ["quote"])

    own_run = start_run(worker["id"], "gerar orçamento", "test/model")
    foreign_run = start_run(outsider["id"], "outra empresa", "test/model")
    assert cancel_run_record(own_run["id"], "falha de teste")
    assert cancel_run_record(foreign_run["id"], "falha externa")

    tools = {tool.__name__: tool for tool in build_company_tools(manager["id"])}
    assert {
        "company_list_runs",
        "company_get_run_checkpoints",
        "company_resume_run",
        "company_create_run_report",
        "company_list_run_reports",
    } <= set(tools)

    rows = json.loads(tools["company_list_runs"]())
    assert {row["id"] for row in rows} == {own_run["id"]}
    with pytest.raises(ValueError, match="não pertence"):
        tools["company_get_run_checkpoints"](foreign_run["id"])

    report = json.loads(
        tools["company_create_run_report"](
            own_run["id"],
            "quote.save",
            "falha de teste",
            "inspecionou estado e preservou a run",
            "failed",
            True,
            "company-task:test",
            "requer análise posterior",
        )
    )
    assert report["company_id"] == acme["id"]
    assert report["agent_id"] == worker["id"]
    assert report["structural_suspected"] is True
    assert list_company_run_reports(acme["id"])[0]["id"] == report["id"]

    runtime = company_runtime_instructions(manager["id"])
    assert "MANAGER RUN RECOVERY" in runtime
    assert "NÃO acione DEV" in runtime
    assert "company_create_run_report" in runtime


def test_run_manager_can_resume_only_company_run(monkeypatch):
    from meuharness.agents import cancel_run_record, start_run
    from meuharness.company_bus import build_company_tools

    acme = create_company({"name": "ACME"})
    other = create_company({"name": "OTHER"})
    manager = _agent("Gerente", acme["id"], ["company-management", "run-management"])
    worker = _agent("Operario", acme["id"], ["quote"])
    outsider = _agent("Externo", other["id"], ["quote"])
    own_run = start_run(worker["id"], "tarefa", "test/model")
    foreign_run = start_run(outsider["id"], "fora", "test/model")
    cancel_run_record(own_run["id"], "interrompida")
    cancel_run_record(foreign_run["id"], "interrompida")

    calls = {}

    async def fake_resume(run_id, *, env_file=None):
        calls["run_id"] = run_id
        return SimpleNamespace(run_id="new-run", text="concluído", model="test/model")

    import meuharness.execution_service as execution_service

    monkeypatch.setattr(execution_service, "resume_run", fake_resume)
    tools = {tool.__name__: tool for tool in build_company_tools(manager["id"])}

    result = json.loads(asyncio.run(tools["company_resume_run"](own_run["id"])))
    assert calls["run_id"] == own_run["id"]
    assert result["new_run_id"] == "new-run"

    with pytest.raises(ValueError, match="não pertence"):
        asyncio.run(tools["company_resume_run"](foreign_run["id"]))


def test_regular_company_agent_does_not_receive_run_manager_tools():
    from meuharness.company_bus import build_company_tools, company_runtime_instructions

    acme = create_company({"name": "ACME"})
    worker = _agent("Operario", acme["id"], ["quote"])
    names = {tool.__name__ for tool in build_company_tools(worker["id"])}
    assert "company_resume_run" not in names
    assert "company_create_run_report" not in names
    assert "MANAGER RUN RECOVERY" not in company_runtime_instructions(worker["id"])
