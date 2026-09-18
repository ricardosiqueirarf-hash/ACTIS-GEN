from pathlib import Path

from meuharness import storage
from meuharness.domains import whatsapp_shadow as shadow


def _use_tmp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def test_whatsapp_shadow_deduplicates_and_searches(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    shadow.upsert_contact("whatsapp-test", "Cemari")
    message = {
        "external_id": "abc-1",
        "direction": "incoming",
        "sender": "Cemari",
        "content": "Pedido 183 precisa de reflecta bronze",
        "timestamp": "2026-09-15T21:00:00-03:00",
    }
    first = shadow.ingest_messages("whatsapp-test", "Cemari", [message])
    second = shadow.ingest_messages("whatsapp-test", "Cemari", [message])
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    rows = shadow.search_messages("whatsapp-test", "reflecta")
    assert len(rows) == 1
    assert rows[0]["content"] == message["content"]


def test_whatsapp_outbox_is_persistent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    item = shadow.queue_message("whatsapp-test", "Cemari", "Bom dia")
    rows = shadow.pending_outbox("whatsapp-test")
    assert rows[0]["id"] == item["id"]
    assert rows[0]["contact_name"] == "Cemari"
    assert rows[0]["status"] == "pending"


def test_whatsapp_inbox_lifecycle_labels_and_intent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    shadow.upsert_contact("wa", "Cemari")
    row = shadow.update_conversation(
        "wa", "Cemari", status="pending", automation_mode="human_only", priority="high"
    )
    assert row["status"] == "pending"
    assert row["automation_mode"] == "human_only"
    assert row["priority"] == "high"
    shadow.add_label("wa", "Cemari", "intent:orcamento")
    shadow.add_label("wa", "Cemari", "team:comercial")
    intent = shadow.set_intent("wa", "Cemari", "orcamento", confidence=0.94, language="pt-BR")
    assert intent["intent"] == "orcamento"
    inbox = shadow.list_inbox("wa", status="pending")
    assert len(inbox) == 1
    assert set(inbox[0]["labels"].split(",")) == {"intent:orcamento", "team:comercial"}


def test_whatsapp_dispatcher_only_marks_sent_after_ack(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(dispatcher, "_authenticated", lambda _page: True)
    monkeypatch.setattr(dispatcher, "_open_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dispatcher, "_send_and_verify", lambda _page, _content: "dom-msg-1")

    result = dispatcher.send_message("wa", "Cemari", "Bom dia")
    assert result["status"] == "sent"
    assert result["attempts"] == 1
    assert result["verified_at"]
    assert result["transport_ref"] == "dom-msg-1"


def test_whatsapp_dispatcher_failure_is_retryable_then_dead(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(dispatcher, "_authenticated", lambda _page: True)
    monkeypatch.setattr(dispatcher, "_open_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dispatcher, "_send_and_verify", lambda *_args: (_ for _ in ()).throw(RuntimeError("no ack")))

    item = shadow.queue_message("wa", "Cemari", "Teste")
    one = dispatcher.dispatch_outbox_item("wa", item["id"], max_attempts=2)
    assert one["status"] == "failed"
    two = dispatcher.dispatch_outbox_item("wa", item["id"], max_attempts=2)
    assert two["status"] == "dead"
    assert two["attempts"] == 2


def test_automation_outbox_respects_human_only_even_at_dispatch_time(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    shadow.update_conversation("wa", "Cemari", automation_mode="human_only")
    item = shadow.queue_message("wa", "Cemari", "Resposta automática", source="automation")
    result = dispatcher.dispatch_outbox_item("wa", item["id"])
    assert result["status"] == "blocked"
    assert result["error"] == "automation_mode:human_only"


def test_global_kill_switch_blocks_automatic_outbox(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    shadow.update_conversation("wa", "Cemari", automation_mode="autonomous")
    shadow.set_automation_enabled("wa", False)
    item = shadow.queue_message("wa", "Cemari", "Auto", source="automation")
    result = dispatcher.dispatch_outbox_item("wa", item["id"])
    assert result["status"] == "blocked"
    assert result["error"] == "global_kill_switch"


def test_handoff_and_resume_are_runtime_state_not_prompt_only(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    handed = shadow.handoff_to_human("wa", "Cemari", reason="cliente pediu humano")
    assert handed["automation_mode"] == "human_only"
    assert handed["status"] == "pending"
    assert any(x["name"] == "team:human" for x in shadow.list_labels("wa", contact_name="Cemari"))
    resumed = shadow.resume_automation("wa", "Cemari", mode="supervised")
    assert resumed["automation_mode"] == "supervised"
    assert not shadow.list_labels("wa", contact_name="Cemari")


def test_whatsapp_sidebar_discovery_returns_unique_conversations(monkeypatch):
    from meuharness.domains import whatsapp_sync as sync

    monkeypatch.setattr(sync, "_whatsapp_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(sync, "_authenticated", lambda _page: True)
    monkeypatch.setattr(
        sync,
        "_eval",
        lambda _page, _script: [
            {"name": "Cemari / Color", "preview": "16:05 · Deu"},
            {"name": "Cemari / Color", "preview": "duplicada"},
            {"name": "COLORGLASS", "preview": "26/08/2026"},
        ],
    )
    result = sync.discover_sidebar_contacts("wa")
    assert result["ok"] is True
    assert [row["name"] for row in result["contacts"]] == ["Cemari / Color", "COLORGLASS"]


def test_whatsapp_sync_does_not_touch_browser_while_agent_is_running(monkeypatch):
    from meuharness.domains import whatsapp_sync as sync

    monkeypatch.setattr(sync, "list_runs", lambda _agent_id: [{"status": "running"}])

    def should_not_open(_agent_id):
        raise AssertionError("worker tentou acessar browser durante run ativa")

    monkeypatch.setattr(sync, "_whatsapp_page", should_not_open)
    result = sync.sync_once("wa")
    assert result == {"ok": False, "reason": "agent_busy", "synced": 0}


def test_whatsapp_outbox_idempotency_key_reuses_same_item(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    first = shadow.queue_message(
        "whatsapp-test", "Cemari", "Bom dia", idempotency_key="run-1:send:cemari:bom-dia"
    )
    second = shadow.queue_message(
        "whatsapp-test", "Cemari", "Bom dia", idempotency_key="run-1:send:cemari:bom-dia"
    )
    assert first["id"] == second["id"]
    with storage.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM whatsapp_outbox").fetchone()["n"]
    assert count == 1


def test_uncertain_outbox_is_never_claimed_for_retry(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    item = shadow.queue_message("whatsapp-test", "Cemari", "Oi")
    claimed = shadow.claim_outbox_id(item["id"])
    assert claimed["status"] == "sending"
    uncertain = shadow.uncertain_outbox(item["id"], "ack ambiguous")
    assert uncertain["status"] == "uncertain"
    assert shadow.claim_outbox("whatsapp-test", limit=10) == []
    assert shadow.claim_outbox_id(item["id"])["status"] == "uncertain"


def test_sync_continuity_records_anchor_and_health(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    shadow.ingest_messages("wa", "Cemari", [
        {"external_id": "m1", "direction": "incoming", "sender": "Cemari", "content": "a", "timestamp": "2026-09-15T20:00:00"},
        {"external_id": "m2", "direction": "incoming", "sender": "Cemari", "content": "b", "timestamp": "2026-09-15T20:01:00"},
    ])
    state = shadow.set_sync_continuity(
        "wa", "Cemari", status="healthy", anchor_external_id="m2", overlap_count=1
    )
    assert state["status"] == "healthy"
    assert state["anchor_external_id"] == "m2"
    assert state["overlap_count"] == 1
    assert state["gap_suspected"] == 0
    assert state["oldest_external_id"] == "m1"
    assert state["newest_external_id"] == "m2"


def test_sync_anchor_overlap_marks_healthy(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_sync as sync
    shadow.upsert_contact("wa", "Cemari", monitored=True)
    shadow.ingest_messages("wa", "Cemari", [
        {"external_id": "m2", "direction": "incoming", "sender": "Cemari", "content": "old", "timestamp": "2026-09-15T20:01:00"}
    ])
    monkeypatch.setattr(sync, "list_runs", lambda _agent_id: [])
    monkeypatch.setattr(sync, "_whatsapp_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(sync, "_authenticated", lambda _page: True)
    monkeypatch.setattr(sync, "_open_contact", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(sync, "_collect_until_anchor", lambda *_args, **_kwargs: {
        "messages": [
            {"external_id": "m2", "direction": "incoming", "sender": "Cemari", "content": "old", "timestamp": "2026-09-15T20:01:00"},
            {"external_id": "m3", "direction": "incoming", "sender": "Cemari", "content": "new", "timestamp": "2026-09-15T20:02:00"},
        ], "anchor": "m2", "overlap_count": 1, "at_top": False, "exhausted": False,
    })
    result = sync.sync_once("wa")
    assert result["contacts"][0]["status"] == "healthy"
    assert result["contacts"][0]["anchor"] == "m2"
    assert result["contacts"][0]["inserted"] == 1


def test_sync_missing_anchor_at_top_marks_gap(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.domains import whatsapp_sync as sync
    shadow.upsert_contact("wa", "Cemari", monitored=True)
    shadow.ingest_messages("wa", "Cemari", [
        {"external_id": "m2", "direction": "incoming", "sender": "Cemari", "content": "old", "timestamp": "2026-09-15T20:01:00"}
    ])
    monkeypatch.setattr(sync, "list_runs", lambda _agent_id: [])
    monkeypatch.setattr(sync, "_whatsapp_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(sync, "_authenticated", lambda _page: True)
    monkeypatch.setattr(sync, "_open_contact", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(sync, "_collect_until_anchor", lambda *_args, **_kwargs: {
        "messages": [{"external_id": "x9", "direction": "incoming", "sender": "Cemari", "content": "unknown", "timestamp": "2026-09-15T20:10:00"}],
        "anchor": None, "overlap_count": 0, "at_top": True, "exhausted": True,
    })
    result = sync.sync_once("wa")
    row = result["contacts"][0]
    assert row["ok"] is False
    assert row["reason"] == "gap_suspected"
    state = shadow.sync_state_for_contact("wa", "Cemari")
    assert state["status"] == "gap_suspected"
    assert state["gap_suspected"] == 1


def test_send_verify_detects_new_message_id_when_match_count_is_unchanged(monkeypatch):
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    snapshots = iter([["old-msg"], ["new-msg"]])
    monkeypatch.setattr(dispatcher, "_matching_outgoing", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(dispatcher, "_focus_composer", lambda *_args: None)
    monkeypatch.setattr(dispatcher, "_composer_text", lambda *_args: "")
    monkeypatch.setattr(dispatcher, "_cdp", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dispatcher, "_wait_until", lambda predicate, **_kwargs: predicate())

    assert dispatcher._send_and_verify({"id": "page"}, "oi") == "new-msg"


def test_whatsapp_send_tool_records_verified_delivery(monkeypatch):
    import json

    from meuharness.adapters.maf.observed_tool import clear_run_tool_trace, get_run_tool_trace
    from meuharness.domains import whatsapp_tools

    monkeypatch.setattr(whatsapp_tools, "get_conversation_by_contact", lambda *_args: {"automation_mode": "supervised"})
    monkeypatch.setattr(whatsapp_tools, "send_message", lambda *_args, **_kwargs: {"status": "sent", "id": "out-1"})
    clear_run_tool_trace("run-wa")
    tools = whatsapp_tools.build_whatsapp_tools("wa", run_id="run-wa")
    send = next(tool for tool in tools if tool.__name__ == "whatsapp_send_message")
    result = json.loads(send("Cemari", "oi"))
    assert result["ok"] is True and result["verified"] is True
    assert get_run_tool_trace("run-wa")[-1] == {"tool": "whatsapp_send_message", "ok": True}


def test_delegated_operator_notification_bypasses_only_supervised_mode(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.agents import create_agent, create_company
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    company = create_company({"name": "ACME", "operator_contact": "85988241620"})
    agent = create_agent({
        "name": "WhatsApp ACME",
        "company_id": company["id"],
        "model": "test/model",
        "instructions": "test",
    })
    shadow.set_automation_enabled(agent["id"], True)
    shadow.update_conversation(agent["id"], "85988241620", automation_mode="supervised")
    shadow.update_conversation(agent["id"], "Outro contato", automation_mode="supervised")

    monkeypatch.setattr(dispatcher, "_page", lambda _agent_id: {"id": "page"})
    monkeypatch.setattr(dispatcher, "_authenticated", lambda _page: True)
    monkeypatch.setattr(dispatcher, "_open_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dispatcher, "_send_and_verify", lambda _page, _content: "dom-operator-1")

    allowed = dispatcher.send_message(
        agent["id"], "85988241620", "Relatório diário", source="delegation"
    )
    assert allowed["status"] == "sent"

    blocked = dispatcher.send_message(
        agent["id"], "Outro contato", "Não deve enviar", source="delegation"
    )
    assert blocked["status"] == "blocked"
    assert blocked["error"] == "automation_mode:supervised"


def test_delegated_operator_report_is_not_compacted(tmp_path, monkeypatch):
    import json

    _use_tmp_db(tmp_path, monkeypatch)
    from meuharness.agents import create_agent, create_company
    from meuharness.domains import whatsapp_tools

    company = create_company({"name": "ACME", "operator_contact": "85988241620"})
    agent = create_agent({
        "name": "WhatsApp ACME",
        "company_id": company["id"],
        "model": "test/model",
        "instructions": "test",
    })
    shadow.update_conversation(agent["id"], "85988241620", automation_mode="supervised")

    captured = {}
    def fake_send(agent_id, contact, content, **kwargs):
        captured.update(agent_id=agent_id, contact=contact, content=content, source=kwargs.get("source"))
        return {"status": "sent", "id": "out-report"}

    monkeypatch.setattr(whatsapp_tools, "send_message", fake_send)
    report = "RELATÓRIO FINANCEIRO\n" + ("linha de detalhe " * 40)
    tools = {t.__name__: t for t in whatsapp_tools.build_whatsapp_tools(agent["id"], source="delegation")}
    result = json.loads(tools["whatsapp_send_message"]("85988241620", report))

    assert result["ok"] is True
    assert captured["content"] == report
    assert len(captured["content"]) > 240
    assert captured["source"] == "delegation"


def test_long_whatsapp_message_gets_extended_ack_window(monkeypatch):
    from meuharness.domains import whatsapp_dispatcher as dispatcher

    snapshots = iter([[], ["new-long-msg"]])
    seen = {}
    monkeypatch.setattr(dispatcher, "_matching_outgoing", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(dispatcher, "_focus_composer", lambda *_args: None)
    monkeypatch.setattr(dispatcher, "_composer_text", lambda *_args: "")
    monkeypatch.setattr(dispatcher, "_cdp", lambda *_args, **_kwargs: None)

    def wait(predicate, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return predicate()

    monkeypatch.setattr(dispatcher, "_wait_until", wait)
    assert dispatcher._send_and_verify({"id": "page"}, "x" * 400) == "new-long-msg"
    assert seen["timeout"] == 45
