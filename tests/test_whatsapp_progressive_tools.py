from types import SimpleNamespace

import pytest

from meuharness import storage
from meuharness.domains import whatsapp_automation as automation
from meuharness.domains import whatsapp_shadow as shadow
from meuharness.domains.whatsapp_tools import build_whatsapp_tools


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    monkeypatch.setenv("ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED", "0")
    from meuharness import agents
    monkeypatch.setattr(agents, "list_runs", lambda _: [])


def _incoming(text: str, ident: str):
    shadow.update_conversation("wa", "A", automation_mode="autonomous")
    shadow.ingest_messages(
        "wa",
        "A",
        [{"external_id": ident, "content": text, "direction": "incoming"}],
        verified_contact=True,
    )
    shadow.set_sync_continuity("wa", "A", status="healthy")


def _execute_stub(captured, execution_service):
    async def execute(agent_id, prompt, **kwargs):
        captured.append(kwargs.get("whatsapp_tool_profile"))
        key = "reply:" + kwargs["whatsapp_delivery_key"]
        queued = shadow.queue_message(
            "wa",
            "A",
            "Resposta",
            source="automation",
            idempotency_key=key,
        )
        shadow.complete_outbox(queued["id"], transport_ref="test")
        return SimpleNamespace(run_id="run-profile")
    return execute


def test_minimal_profile_exposes_only_send_and_handoff():
    names = {
        tool.__name__
        for tool in build_whatsapp_tools(
            "wa",
            source="automation",
            contact_scope="A",
            delivery_key="job",
            profile="minimal",
        )
    }
    assert names == {"whatsapp_send_message", "whatsapp_send_document", "whatsapp_handoff"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Oi, tudo bem?", "minimal"),
        ("Vocês fazem cristaleira?", "minimal"),
        ("Quero orçar 3 portas", "quote"),
        ("2 portas de giro, bronze, espelho prata, 300x200", "quote"),
        ("Qual status do pedido 513?", "operational"),
        ("Quem não pagou?", "operational"),
        ("Mova o pedido 154 para produção", "operational"),
    ],
)
def test_deterministic_profile_classifier(text, expected):
    assert automation._whatsapp_tool_profile(text, is_operator=True) == expected


def test_simple_inbound_uses_minimal_tools(monkeypatch):
    from meuharness import execution_service

    captured = []
    monkeypatch.setattr(
        execution_service,
        "execute_agent",
        _execute_stub(captured, execution_service),
    )
    _incoming("Oi, tudo bem?", "simple-1")

    result = automation.process_inbound_once("wa", debounce_seconds=0)

    assert result["ok"] is True
    assert captured == ["minimal"]


def test_operational_inbound_loads_actis_tools(monkeypatch):
    from meuharness import execution_service

    captured = []
    monkeypatch.setattr(
        execution_service,
        "execute_agent",
        _execute_stub(captured, execution_service),
    )
    _incoming("Qual status do pedido 513?", "operational-1")

    result = automation.process_inbound_once("wa", debounce_seconds=0)

    assert result["ok"] is True
    assert captured == ["operational"]

def test_quote_inbound_uses_quote_profile_not_legacy_dialog(monkeypatch):
    from meuharness import execution_service

    captured = []
    monkeypatch.setattr(
        execution_service,
        "execute_agent",
        _execute_stub(captured, execution_service),
    )
    monkeypatch.setattr(
        automation,
        "_quote_runtime_decision",
        lambda *args, **kwargs: pytest.fail("legacy deterministic quote dialog must stay off"),
    )
    _incoming("Quero orçar 3 portas de giro", "quote-profile-1")

    result = automation.process_inbound_once("wa", debounce_seconds=0)

    assert result["ok"] is True
    assert captured == ["quote"]
