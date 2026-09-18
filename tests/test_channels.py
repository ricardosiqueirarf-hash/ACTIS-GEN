from pathlib import Path

from meuharness import channels, storage


def _use_tmp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def test_default_general_channel_is_global(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    rows = channels.list_channels()
    assert len(rows) == 1
    assert rows[0]["name"] == "#general"
    assert rows[0]["scope_type"] == "global"


def test_long_term_message_becomes_agent_context(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    general = channels.list_channels()[0]
    channels.create_channel_message(general["id"], {
        "kind": "decision", "content": "Usar SQLite como memória operacional principal.",
        "pinned": True,
    })
    items = channels.channel_context_for_agent({"id": "agent-a", "name": "A"}, "memória operacional")
    assert len(items) == 1
    assert items[0]["kind"] == "decision"
    assert items[0]["pinned"] == 1


def test_members_channel_only_applies_to_members(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    channel = channels.create_channel({
        "name": "produto", "topic": "Direção de produto", "scope_type": "members",
        "members": ["agent-a"],
    })
    channels.create_channel_message(channel["id"], {
        "kind": "rule", "content": "Sempre preservar compatibilidade retroativa.",
    })
    a_items = channels.channel_context_for_agent({"id": "agent-a"}, "compatibilidade")
    b_items = channels.channel_context_for_agent({"id": "agent-b"}, "compatibilidade")
    assert any(item["channel_name"] == "#produto" for item in a_items)
    assert not any(item["channel_name"] == "#produto" for item in b_items)


def test_channel_message_can_be_completed_and_removed_from_context(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    general = channels.list_channels()[0]
    item = channels.create_channel_message(general["id"], {
        "kind": "goal", "content": "Migrar o sync para continuidade verificável.",
    })
    assert channels.channel_context_for_agent({"id": "agent-a"}, "sync")
    updated = channels.update_channel_message(item["id"], {"status": "done"})
    assert updated and updated["status"] == "done"
    assert channels.channel_context_for_agent({"id": "agent-a"}, "sync") == []
