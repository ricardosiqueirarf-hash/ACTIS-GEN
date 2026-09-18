"""Production regressions: no live WhatsApp or customer traffic."""
import json

import pytest

from meuharness import storage
from meuharness.domains import whatsapp_dispatcher as dispatcher
from meuharness.domains import whatsapp_shadow as shadow


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")

def fake_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher, "_page", lambda _: {"id": "test"})
    monkeypatch.setattr(dispatcher, "_authenticated", lambda _: True)
    monkeypatch.setattr(dispatcher, "_open_chat", lambda *a: None)
    monkeypatch.setattr(dispatcher, "_send_and_verify", lambda *a: calls.append(a) or "ack-1")
    return calls

def test_ingest_preserves_monitoring_and_backfill_preferences():
    shadow.upsert_contact("wa", "Ricardo", monitored=False, sync_history=True)
    shadow.ingest_messages("wa", "Ricardo", [], emit_inbound_events=False)
    row = shadow.get_contact("wa", "Ricardo")
    assert row["monitored"] == 0
    assert row["sync_history"] == 1

def test_backfill_cannot_regress_latest_customer_message():
    for ident, date in [("new", "2026-09-17T10:00:00"), ("old", "2026-09-01T10:00:00")]:
        shadow.ingest_messages("wa", "Ricardo", [{"external_id": ident,
            "direction": "incoming", "content": ident, "timestamp": date}],
            emit_inbound_events=False)
    row = shadow.get_conversation_by_contact("wa", "Ricardo")
    assert row["last_message_at"] == "2026-09-17T10:00:00"
    assert row["last_customer_at"] == "2026-09-17T10:00:00"

def test_already_claimed_item_is_not_sent_by_second_dispatcher(monkeypatch):
    calls = fake_transport(monkeypatch)
    item = shadow.queue_message("wa", "Ricardo", "teste")
    shadow.claim_outbox_id(item["id"])
    result = dispatcher.dispatch_outbox_item("wa", item["id"])
    assert result["status"] == "sending"
    assert calls == []

def test_foreign_agent_cannot_claim_another_outbox(monkeypatch):
    fake_transport(monkeypatch)
    item = shadow.queue_message("owner", "Ricardo", "teste")
    with pytest.raises(ValueError):
        dispatcher.dispatch_outbox_item("other", item["id"])
    after = shadow.outbox_item(item["id"])
    assert after["status"] == "pending"
    assert after["attempts"] == 0

@pytest.mark.parametrize("content", ["", "   ", "\n\t"])
def test_empty_message_rejected_before_queue(content):
    with pytest.raises(ValueError):
        shadow.queue_message("wa", "Ricardo", content)

def test_same_idempotency_key_cannot_retarget_message():
    shadow.queue_message("wa", "Ricardo", "original", idempotency_key="same")
    with pytest.raises(ValueError):
        shadow.queue_message("wa", "Outro", "different", idempotency_key="same")

def test_contact_name_with_digits_is_not_a_phone():
    assert dispatcher._phone(None, "Pedido 20260917 cliente 12345") == ""

def test_numeric_contact_gets_canonical_phone():
    row = shadow.upsert_contact("wa", "85988241620")
    assert row["external_id"] == "5585988241620"

def test_transport_error_after_enter_is_ambiguous(monkeypatch):
    monkeypatch.setattr(dispatcher, "_matching_outgoing", lambda *a: [])
    monkeypatch.setattr(dispatcher, "_focus_composer", lambda *a: None)
    monkeypatch.setattr(dispatcher, "_composer_text", lambda *a: "")
    def cdp(page, method, params):
        if method == "Input.dispatchKeyEvent":
            raise OSError("connection lost after sending Enter")
    monkeypatch.setattr(dispatcher, "_cdp", cdp)
    with pytest.raises(dispatcher.AmbiguousDeliveryError):
        dispatcher._send_and_verify({}, "teste")

def test_existing_draft_is_preserved(monkeypatch):
    monkeypatch.setattr(dispatcher, "_matching_outgoing", lambda *a: [])
    monkeypatch.setattr(dispatcher, "_focus_composer", lambda *a: None)
    monkeypatch.setattr(dispatcher, "_composer_text", lambda *a: "rascunho do operador")
    calls = []
    monkeypatch.setattr(dispatcher, "_cdp", lambda *a: calls.append(a))
    with pytest.raises(RuntimeError, match="rascunho"):
        dispatcher._send_and_verify({}, "teste")
    assert calls == []

def test_context_is_persistent_and_scoped():
    shadow.ingest_messages("wa", "Ricardo", [{"external_id":"e1",
        "direction":"incoming", "content":"Quero orçamento"}], emit_inbound_events=False)
    saved = shadow.save_customer_context("wa", "Ricardo", summary="Contato de teste",
        current_subject="Orçamento", pending_items=["confirmar medidas"],
        facts={"uso":"teste"}, evidence_ids=["e1"])
    assert saved["summary"] == "Contato de teste"
    loaded = shadow.customer_context("wa", "Ricardo")
    assert loaded["pending_items"] == ["confirmar medidas"]
    assert loaded["facts"] == {"uso":"teste"}
    assert shadow.customer_context("other", "Ricardo") is None

def test_context_accepts_structured_json_facts():
    shadow.ingest_messages("wa", "Ricardo", [{"external_id":"e-json",
        "direction":"incoming", "content":"Mensagem de teste"}], emit_inbound_events=False)
    saved = shadow.save_customer_context("wa", "Ricardo", summary="Contexto estruturado",
        facts={"autorizado_para_teste": True, "documentos": ["orcamento.pdf"],
               "meta": {"origem": "whatsapp"}}, evidence_ids=["e-json"])
    assert saved["facts"]["autorizado_para_teste"] is True
    assert saved["facts"]["documentos"] == ["orcamento.pdf"]
    assert saved["facts"]["meta"] == {"origem": "whatsapp"}

def test_context_rejects_unknown_evidence():
    shadow.upsert_contact("wa", "Ricardo")
    with pytest.raises(ValueError, match="evid"):
        shadow.save_customer_context("wa", "Ricardo", summary="x", evidence_ids=["invented"])

def test_search_accepts_punctuation_without_fts_syntax_errors():
    shadow.ingest_messages("wa", "Ricardo", [{"external_id":"e1",
        "direction":"incoming","content":"Preciso de perfil 070"}], emit_inbound_events=False)
    assert shadow.search_messages("wa", "perfil: 070?", contact_name="Ricardo")

def test_automatic_run_cannot_release_human_control():
    from meuharness.domains.whatsapp_tools import build_whatsapp_tools
    shadow.handoff_to_human("wa", "Ricardo", reason="humano")
    tools = {t.__name__: t for t in build_whatsapp_tools("wa", source="automation")}
    result = json.loads(tools["whatsapp_resume"]("Ricardo", "autonomous"))
    assert result["ok"] is False
    assert shadow.get_conversation_by_contact("wa", "Ricardo")["automation_mode"] == "human_only"

def test_selected_sync_does_not_visit_other_contacts(monkeypatch):
    from meuharness.domains import whatsapp_sync as sync
    shadow.upsert_contact("wa", "Ricardo")
    shadow.upsert_contact("wa", "Other")
    monkeypatch.setattr(sync, "list_runs", lambda _: [])
    monkeypatch.setattr(sync, "_whatsapp_page", lambda _: {"id":"test"})
    monkeypatch.setattr(sync, "_authenticated", lambda _: True)
    opened = []
    monkeypatch.setattr(sync, "_open_contact", lambda p, n, e: opened.append(n) or True)
    monkeypatch.setattr(sync, "_scrape_messages", lambda _: [])
    result = sync.sync_once("wa", contact_name="Ricardo", emit_inbound_events=False)
    assert opened == ["Ricardo"]
    assert result["ok"] is True

def test_idempotency_is_atomic_under_concurrent_requests():
    from concurrent.futures import ThreadPoolExecutor
    shadow.upsert_contact("wa", "Ricardo")
    def queued(_):
        return shadow.queue_message("wa", "Ricardo", "once", idempotency_key="concurrent")["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(queued, range(4)))
    assert len(set(ids)) == 1

def test_browser_lease_prevents_parallel_transport():
    from meuharness.domains.whatsapp_transport import BrowserBusyError, browser_lease
    with browser_lease("wa"), pytest.raises(BrowserBusyError), browser_lease("wa"):
        pytest.fail("two writers entered")

def test_worker_never_dispatches_operator_drafts(monkeypatch):
    calls = fake_transport(monkeypatch)
    item = shadow.queue_message("wa", "Ricardo", "rascunho", source="user")
    assert dispatcher.dispatch_once("wa") == []
    assert calls == []
    assert shadow.outbox_item(item["id"])["status"] == "pending"

def test_quarantine_excludes_cross_contact_history_until_verified():
    message = {"external_id": "same", "content": "privado", "direction": "incoming"}
    shadow.ingest_messages("wa", "A", [message], emit_inbound_events=False)
    shadow.ingest_messages("wa", "B", [message], emit_inbound_events=False)
    result = shadow.quarantine_ambiguous_messages("wa")
    assert result["deleted"] == 0
    assert shadow.recent_messages("wa") == []
    assert shadow.search_messages("wa", "privado") == []
    shadow.ingest_messages("wa", "A", [message], emit_inbound_events=False, verified_contact=True)
    assert len(shadow.recent_messages("wa", contact_name="A")) == 1
    assert shadow.recent_messages("wa", contact_name="B") == []

def test_verified_sync_corrects_legacy_outgoing_direction():
    message = {"external_id":"same","content":"oi","direction":"incoming"}
    shadow.ingest_messages("wa","A",[message],emit_inbound_events=False)
    message["direction"] = "outgoing"
    shadow.ingest_messages("wa","A",[message],emit_inbound_events=False,verified_contact=True)
    assert shadow.recent_messages("wa",contact_name="A")[0]["direction"] == "outgoing"

def test_old_backfill_never_emits_new_customer_request(monkeypatch):
    events = []
    monkeypatch.setattr(shadow,"emit_event",lambda name,**k: events.append(name))
    shadow.ingest_messages("wa","A",[{"external_id":"new","content":"agora",
        "direction":"incoming","timestamp":"2026-09-17T12:00:00"}],emit_inbound_events=False)
    events.clear()
    shadow.ingest_messages("wa","A",[{"external_id":"old","content":"antiga",
        "direction":"incoming","timestamp":"2026-08-01T12:00:00"}])
    assert "whatsapp.message.inbound" not in events
    assert shadow.get_conversation_by_contact("wa","A")["unread_count"] == 0

def test_native_negative_result_is_observed_as_failure(monkeypatch):
    from meuharness.adapters.maf import observed_tool as observed
    events = []
    monkeypatch.setattr(observed,"emit_event",lambda name,**k:events.append(name))
    monkeypatch.setattr(observed,"set_run_activity",lambda *a,**k:None)
    monkeypatch.setattr(observed,"update_work_by_run",lambda *a,**k:None)
    def blocked():
        return json.dumps({"ok":False,"status":"blocked"})
    observed.clear_run_tool_trace("negative")
    wrapped=observed.observe_native_tool(blocked,run_id="negative",agent_id="wa")
    wrapped()
    assert "tool.failed" in events and "tool.completed" not in events
    assert observed.get_run_tool_trace("negative")[-1]["ok"] is False

def test_general_discovers_context_and_sync_via_whatsapp_agent():
    from meuharness.domains.whatsapp_tools import build_whatsapp_tools
    from meuharness.feature_registry import list_feature_catalog
    feature=next(r for r in list_feature_catalog() if r["id"]=="whatsapp")
    assert feature["general_mode"]=="hybrid"
    names={t.__name__ for t in build_whatsapp_tools("wa")}
    for tool in ["whatsapp_open","whatsapp_sync_contact","whatsapp_customer_context","whatsapp_save_context"]:
        assert tool in feature["notes"]
        assert tool in names

def test_null_timestamp_backfill_does_not_override_known_recency():
    shadow.ingest_messages("wa", "Ricardo", [{
        "external_id": "new", "direction": "incoming", "content": "mensagem nova",
        "timestamp": "2026-09-17T10:00:00",
    }], emit_inbound_events=False)
    shadow.ingest_messages("wa", "Ricardo", [{
        "external_id": "old-doc", "direction": "incoming", "content": "PDF antigo",
        "timestamp": None, "message_type": "document",
    }], emit_inbound_events=False)
    recent = shadow.recent_messages("wa", contact_name="Ricardo")
    inbox = shadow.list_inbox("wa")
    state = shadow.set_sync_continuity("wa", "Ricardo", status="healthy")
    conversation = shadow.get_conversation_by_contact("wa", "Ricardo")
    assert recent[0]["external_id"] == "new"
    assert inbox[0]["last_message_content"] == "mensagem nova"
    assert state["newest_external_id"] == "new"
    assert state["last_message_ts"] == "2026-09-17T10:00:00"
    assert conversation["last_customer_at"] == "2026-09-17T10:00:00"

def test_whatsapp_timestamp_with_us_month_day_order_is_normalized():
    assert shadow._normalize_message_timestamp("2026-17-09T04:21:00") == "2026-09-17T04:21:00"
    assert shadow._normalize_message_timestamp("2026-09-17T04:21:00") == "2026-09-17T04:21:00"

def test_local_whatsapp_timestamp_is_compared_as_machine_timezone():
    assert shadow._timestamp_is_newer(
        '2026-09-17T21:35:00',
        '2026-09-18T00:34:52.197979+00:00',
    )
    assert not shadow._timestamp_is_newer(
        '2026-09-17T21:34:00',
        '2026-09-18T00:34:52.197979+00:00',
    )
