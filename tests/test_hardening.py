from pathlib import Path

from meuharness import storage


def use_temp_db(monkeypatch, tmp_path: Path) -> Path:
    db = tmp_path / "actis-test.db"
    monkeypatch.setattr(storage, "DB_FILE", db)
    return db


def test_sqlite_collections_are_canonical(monkeypatch, tmp_path):
    db = use_temp_db(monkeypatch, tmp_path)
    storage.save_collection("agents", [{"id": "a", "name": "Agent"}])
    assert db.is_file()
    assert storage.load_collection("agents") == [{"id": "a", "name": "Agent"}]


def test_persistent_memory_recall(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    from meuharness.memory import recall, remember

    remember("agent-a", "Cliente prefere pagamento via Pix e entrega pela manhã.")
    remember("agent-a", "Projeto interno usa alumínio preto.")
    hits = recall("agent-a", "qual a preferência de pagamento do cliente?", limit=3)
    assert hits
    assert "Pix" in hits[0]["content"]


def test_granular_approval_lifecycle(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    from meuharness.approvals import approved_scopes, request_approval, resolve_approval

    approval = request_approval("agent-a", "files.write", "precisa salvar relatório")
    assert approval["status"] == "pending"
    resolved = resolve_approval(approval["id"], True)
    assert resolved["status"] == "approved"
    assert "files.write" in approved_scopes("agent-a")


def test_workflow_graph_persistence(monkeypatch, tmp_path):
    use_temp_db(monkeypatch, tmp_path)
    from meuharness.workflows import get_workflow, save_workflow

    workflow = save_workflow({
        "name": "Pipeline",
        "nodes": [{"id": "n1", "agent_id": "general", "x": 10, "y": 20}],
        "edges": [],
    })
    loaded = get_workflow(workflow["id"])
    assert loaded is not None
    assert loaded["name"] == "Pipeline"
    assert loaded["nodes"][0]["agent_id"] == "general"


def test_explicit_agent_permissions_restrict_tool_scopes(monkeypatch):
    from meuharness import execution_service
    from meuharness.execution_service import _effective_scopes

    monkeypatch.setattr(execution_service, "approved_scopes", lambda _agent_id: set())
    read_only = {
        "id": "scope-test",
        "tools": ["harness_files"],
        "permissions": ["files.read"],
    }
    assert _effective_scopes(read_only, ["harness_files"]) == {"files.read"}

    legacy = {"id": "scope-test", "tools": ["harness_files"]}
    assert _effective_scopes(legacy, ["harness_files"]) == {"files.read", "files.write"}


def test_browser_manager_uses_stable_distinct_agent_profiles(monkeypatch, tmp_path):
    from meuharness import browser_manager as manager

    monkeypatch.setattr(manager, "DATA_DIR", tmp_path)
    monkeypatch.setattr(manager, "BROWSER_DIR", tmp_path / "browser-profiles")
    monkeypatch.setattr(manager, "LOCK_FILE", tmp_path / "browser-manager.lock")
    assert manager._safe_id("Financeiro / CE") == "Financeiro-CE"
    assert manager._preferred_port("agent-a") != manager._preferred_port("agent-b")
    assert manager.BROWSER_DIR / manager._safe_id("agent-a") != manager.BROWSER_DIR / manager._safe_id("agent-b")


def test_browser_tool_targets_managed_agent_runtime(monkeypatch):
    from types import SimpleNamespace

    from meuharness.adapters.maf import browser_tools

    captured = {}
    monkeypatch.setattr(
        browser_tools,
        "ensure_browser",
        lambda agent_id: SimpleNamespace(
            cdp_url="http://127.0.0.1:9555",
            profile_dir="/tmp/profile-a",
            agent_id=agent_id,
        ),
    )

    class FakeTool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(browser_tools, "ObservedMCPStdioTool", FakeTool)
    browser_tools.build_harness_browser({"browser.read"}, agent_id="agent-a", run_id="run-a")
    assert captured["env"]["BU_CDP_URL"] == "http://127.0.0.1:9555"
    assert captured["env"]["ACTIS_BROWSER_PROFILE"] == "/tmp/profile-a"
    assert captured["env"]["ACTIS_BROWSER_AGENT_ID"] == "agent-a"
