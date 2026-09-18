import pytest

from meuharness import storage
from meuharness.domains import whatsapp_baileys as baileys
from meuharness.domains import whatsapp_dispatcher as dispatcher
from meuharness.domains import whatsapp_shadow as shadow


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")


def _queued():
    return shadow.queue_message(
        "wa",
        "85988241620",
        "teste transporte",
        source="user",
        idempotency_key="transport-test",
    )


def _fake_web(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher, "_page", lambda _: {"id": "web"})
    monkeypatch.setattr(dispatcher, "_authenticated", lambda _: True)
    monkeypatch.setattr(dispatcher, "_open_chat", lambda *args: calls.append(("open", args)))
    monkeypatch.setattr(
        dispatcher,
        "_send_and_verify",
        lambda page, content: calls.append(("send", content)) or "web-ack",
    )
    return calls


def test_baileys_acceptance_never_touches_web(monkeypatch):
    item = _queued()
    monkeypatch.setattr(baileys, "health", lambda: {"connected": True, "status": "open"})
    monkeypatch.setattr(
        baileys,
        "send_outbox",
        lambda row: {
            "ok": True,
            "status": "accepted",
            "attempted": True,
            "safe_fallback": False,
            "message_id": "BAILEYS-1",
        },
    )
    monkeypatch.setattr(
        dispatcher,
        "_page",
        lambda _: pytest.fail("web fallback must not run after Baileys acceptance"),
    )

    result = dispatcher.dispatch_outbox_item("wa", item["id"])

    assert result["status"] == "sent"
    assert result["transport"] == "baileys"
    assert result["transport_ref"] == "baileys:BAILEYS-1"
    assert result["attempts"] == 1


def test_primary_unavailable_uses_web_fallback(monkeypatch):
    item = _queued()
    monkeypatch.setattr(
        baileys,
        "health",
        lambda: {"connected": False, "status": "reconnecting"},
    )
    calls = _fake_web(monkeypatch)

    result = dispatcher.dispatch_outbox_item("wa", item["id"])

    assert result["status"] == "sent"
    assert result["transport"] == "web_fallback"
    assert result["transport_ref"] == "web-ack"
    assert [kind for kind, _ in calls] == ["open", "send"]


def test_safe_pre_send_failure_falls_back_without_burning_attempt(monkeypatch):
    item = _queued()
    monkeypatch.setattr(baileys, "health", lambda: {"connected": True, "status": "open"})
    monkeypatch.setattr(
        baileys,
        "send_outbox",
        lambda row: {
            "ok": False,
            "status": "unavailable",
            "attempted": False,
            "safe_fallback": True,
            "error": "socket closed before send",
        },
    )
    _fake_web(monkeypatch)

    result = dispatcher.dispatch_outbox_item("wa", item["id"])

    assert result["status"] == "sent"
    assert result["transport"] == "web_fallback"
    assert result["attempts"] == 1


def test_ambiguous_baileys_send_never_falls_back(monkeypatch):
    item = _queued()
    monkeypatch.setattr(baileys, "health", lambda: {"connected": True, "status": "open"})
    monkeypatch.setattr(
        baileys,
        "send_outbox",
        lambda row: {
            "ok": False,
            "status": "ambiguous",
            "attempted": True,
            "safe_fallback": False,
            "error": "connection dropped after send call",
        },
    )
    monkeypatch.setattr(
        dispatcher,
        "_page",
        lambda _: pytest.fail("ambiguous Baileys send must never retry through Web"),
    )

    result = dispatcher.dispatch_outbox_item("wa", item["id"])

    assert result["status"] == "uncertain"
    assert result["transport"] == "baileys"
    assert result["attempts"] == 1


def test_baileys_inbound_is_ingested_into_existing_contact(monkeypatch):
    shadow.upsert_contact("wa", "Ricardo", external_id="5585988241620")
    events = [{
        "seq": 1,
        "message_id": "BAILEYS-IN-1",
        "remote_jid": "5585988241620@s.whatsapp.net",
        "phone": "5585988241620",
        "peer_kind": "direct",
        "direction": "incoming",
        "sender": "Ricardo",
        "content": "Mensagem real",
        "message_type": "text",
        "message_ts": "2026-09-18T10:00:00+00:00",
    }]
    monkeypatch.setattr(baileys, "health", lambda: {"connected": True, "status": "open"})
    monkeypatch.setattr(baileys, "_events", lambda after, limit=200: events if after == 0 else [])

    first = baileys.sync_once("wa")
    second = baileys.sync_once("wa")
    recent = shadow.recent_messages("wa", contact_name="Ricardo")

    assert first["ok"] is True
    assert first["synced"] == 1
    assert first["cursor"] == 1
    assert second["synced"] == 0
    assert recent[0]["external_id"] == "BAILEYS-IN-1"
    assert recent[0]["content"] == "Mensagem real"

def test_recipient_prefers_observed_remote_jid(monkeypatch):
    item = _queued()
    baileys._remember_peer(
        "wa", "85988241620", "261572232532189@lid", "558588241620", "direct"
    )
    resolved = baileys._recipient_payload(shadow.outbox_item(item["id"]))
    assert resolved == {"jid": "261572232532189@lid"}
