import meuharness.project_memory as pm
from meuharness import contexts


def test_project_memory_catalog_is_metadata_only_and_recall_loads_selected(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    pm.init_project_memory(workspace)

    observation = pm.write_project_memory(
        workspace,
        kind="observation",
        title="OAuth refresh loop",
        summary="Refresh token entra em loop quando o provider volta 401.",
        content="Foi observado um loop após resposta 401 do provider durante refresh.",
        tags=["oauth", "provider"],
    )
    pm.write_project_memory(
        workspace,
        kind="decision",
        title="Browser profile isolation",
        summary="Cada agente WhatsApp usa profile isolado.",
        content="Perfis de browser não devem ser compartilhados entre agentes WhatsApp.",
        tags=["browser", "whatsapp"],
    )

    catalog = pm.catalog_project_memory(workspace)
    assert len(catalog) == 2
    assert all("content" not in row for row in catalog)
    assert observation["status"] == "unverified"
    index = (workspace / pm.MEMORY_DIRNAME / "INDEX.md").read_text(encoding="utf-8")
    assert "metadata only" in index
    assert "OAuth refresh loop" in index

    selected = pm.recall_project_memory(workspace, "provider oauth 401", limit=4)
    assert [row["id"] for row in selected] == [observation["id"]]
    assert "loop após resposta 401" in selected[0]["content"]
    promoted = pm.promote_observation(
        workspace,
        observation["id"],
        target_kind="gotcha",
        verification_note="Reproduzido duas vezes e confirmado nos logs do provider.",
    )
    source = pm.read_project_memory(workspace, observation["id"])
    target = pm.read_project_memory(workspace, promoted["promoted"]["id"])
    assert source is not None and source["status"] == "promoted"
    assert source["promoted_to"] == promoted["promoted"]["id"]
    assert target is not None and target["kind"] == "gotcha"
    assert "Reproduzido duas vezes" in target["content"]


def test_project_memory_for_agent_uses_project_binding_and_progressive_recall(tmp_path, monkeypatch):
    workspace = tmp_path / "actis"
    workspace.mkdir()
    memory = pm.write_project_memory(
        workspace,
        kind="decision",
        title="Headless WhatsApp sandbox",
        summary="WhatsApp headless deve manter sandbox por agente.",
        content="Use um profile separado para cada identidade WhatsApp.",
        tags=["headless", "whatsapp"],
    )
    monkeypatch.setattr(
        contexts,
        "list_agent_bindings",
        lambda agent_id=None: [
            {"agent_id": "agent-1", "scope_type": "project", "scope_id": "project-1"}
        ],
    )
    monkeypatch.setattr(
        pm,
        "list_project_memory_bindings",
        lambda project_id=None: [
            {"project_id": "project-1", "workspace_path": str(workspace)}
        ],
    )

    items = pm.project_memory_for_agent("agent-1", "whatsapp headless")
    assert len(items) == 1
    assert items[0]["id"] == memory["id"]
    assert items[0]["project_id"] == "project-1"
    instructions = pm.project_memory_instructions(items)
    assert "MEMÓRIA MARKDOWN RELEVANTE DOS PROJETOS" in instructions
    assert "observations como não verificadas" in instructions
