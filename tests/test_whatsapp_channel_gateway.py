from types import SimpleNamespace

import pytest

from meuharness import storage
from meuharness.domains import whatsapp_channel_gateway as gateway
from meuharness.domains import whatsapp_shadow as shadow


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    monkeypatch.setenv("ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED", "1")


def test_disabled_shadow_routes_nothing(monkeypatch):
    gateway.upsert_binding("prod", "shadow")
    monkeypatch.setenv("ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED", "0")
    result = gateway.route_inbound_message(
        "prod", "Cliente", {"message_id": "m1", "content": "oi"}
    )
    assert result == []


def test_route_is_idempotent_and_session_is_agent_scoped():
    gateway.upsert_binding("prod", "shadow-a")
    gateway.upsert_binding("prod", "shadow-b")
    message = {
        "message_id": "m1",
        "external_id": "ext-1",
        "content": "Quero orçamento",
        "sender": "Cliente",
    }
    first = gateway.route_inbound_message("prod", "Cliente", message)
    second = gateway.route_inbound_message("prod", "Cliente", message)
    assert len(first) == 2
    assert {row["id"] for row in first} == {row["id"] for row in second}
    assert len({row["session_key"] for row in first}) == 2
    assert all(":whatsapp:account:prod:direct:Cliente" in row["session_key"] for row in first)


def test_same_inbound_reaches_production_job_and_shadow_job():
    from meuharness.domains import whatsapp_automation as automation

    gateway.upsert_binding("prod", "shadow")
    shadow.update_conversation("prod", "Cliente", automation_mode="autonomous")
    shadow.ingest_messages(
        "prod",
        "Cliente",
        [{
            "external_id": "wa-both-1",
            "content": "Quero uma porta",
            "direction": "incoming",
            "sender": "Cliente",
        }],
        verified_contact=True,
    )
    assert len(automation.list_jobs("prod")) == 1
    shadow_jobs = gateway.list_shadow_jobs("prod")
    assert len(shadow_jobs) == 1
    assert shadow_jobs[0]["agent_id"] == "shadow"


def test_ingest_fans_out_without_creating_shadow_outbox(monkeypatch):
    gateway.upsert_binding("prod", "shadow")
    shadow.update_conversation("prod", "Cliente", automation_mode="supervised")
    result = shadow.ingest_messages(
        "prod",
        "Cliente",
        [{
            "external_id": "wa-1",
            "content": "Olá",
            "direction": "incoming",
            "sender": "Cliente",
        }],
        verified_contact=True,
    )
    jobs = gateway.list_shadow_jobs("prod")
    assert result["incoming_inserted"] == 1
    assert len(jobs) == 1
    assert jobs[0]["agent_id"] == "shadow"
    assert shadow.pending_outbox("prod") == []
    assert shadow.pending_outbox("shadow") == []


def test_shadow_execution_persists_candidate_and_has_no_whatsapp_scope(monkeypatch):
    from meuharness import agents, execution_service

    gateway.upsert_binding("prod", "shadow")
    job = gateway.route_inbound_message(
        "prod", "Cliente", {"message_id": "m1", "content": "Qual o prazo?"}
    )[0]

    monkeypatch.setattr(
        agents,
        "get_agent",
        lambda agent_id: {
            "id": agent_id,
            "name": "Shadow",
            "company_id": None,
            "tools": [],
            "skills": [],
            "permissions": [],
        },
    )
    captured = {}

    async def fake_execute(agent_id, prompt, **kwargs):
        captured["agent_id"] = agent_id
        captured["prompt"] = prompt
        captured.update(kwargs)
        return SimpleNamespace(run_id="run-shadow", text="Qual medida da porta?")

    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    result = gateway.process_shadow_once("prod", peer_id="Cliente")
    saved = gateway.list_shadow_jobs("prod")[0]

    assert result["ok"] is True
    assert result["response"] == "Qual medida da porta?"
    assert captured["source"] == "automation"
    assert captured["conversation_id"] == job["session_key"]
    assert captured["disable_tools"] is True
    assert "whatsapp_contact" not in captured
    assert "whatsapp_delivery_key" not in captured
    assert saved["status"] == "completed"
    assert saved["response"] == "Qual medida da porta?"
    assert shadow.pending_outbox("shadow") == []


def test_shadow_agent_with_company_or_tools_is_blocked(monkeypatch):
    from meuharness import agents, execution_service

    gateway.upsert_binding("prod", "unsafe-shadow")
    gateway.route_inbound_message(
        "prod", "Cliente", {"message_id": "m1", "content": "oi"}
    )
    monkeypatch.setattr(
        agents,
        "get_agent",
        lambda agent_id: {
            "id": agent_id,
            "company_id": "colorglass",
            "tools": [],
            "skills": [],
            "permissions": [],
        },
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("unsafe shadow should not execute")

    monkeypatch.setattr(execution_service, "execute_agent", forbidden)
    result = gateway.process_shadow_once("prod")
    assert result["status"] == "blocked"
    assert result["reason"] == "shadow_agent_not_isolated"
