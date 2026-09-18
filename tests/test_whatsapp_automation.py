"""Reactive delivery uses isolated state and a stubbed canonical agent."""
import json
from types import SimpleNamespace

import pytest

from meuharness import storage
from meuharness.domains import whatsapp_automation as automation
from meuharness.domains import whatsapp_shadow as shadow
from meuharness.domains.whatsapp_tools import build_whatsapp_tools


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,"DATA_DIR",tmp_path)
    monkeypatch.setattr(storage,"DB_FILE",tmp_path/"actis.db")
    from meuharness import agents
    monkeypatch.setattr(agents,"list_runs",lambda _:[])

def incoming(contact="A",ident="in1",mode="autonomous"):
    shadow.update_conversation("wa",contact,automation_mode=mode)
    shadow.ingest_messages("wa",contact,[{"external_id":ident,
        "content":"Quero informações","direction":"incoming"}],verified_contact=True)
    shadow.set_sync_continuity("wa",contact,status="healthy")
    return automation.list_jobs("wa")

def test_supervised_contact_does_not_start_automatic_reply():
    assert incoming(mode="supervised")==[]

def test_global_pause_does_not_enqueue_replies():
    shadow.set_automation_enabled("wa",False)
    assert incoming()==[]

def test_incoming_deduplication_creates_one_job():
    incoming()
    incoming()
    assert len(automation.list_jobs("wa"))==1


def test_restart_recovery_requeues_orphaned_processing_job():
    job = incoming()[0]
    with storage.connect() as conn:
        conn.execute(
            "UPDATE whatsapp_inbound_jobs SET status='processing',started_at=? WHERE id=?",
            ("2026-09-18T10:00:00+00:00", job["id"]),
        )
    recovered = automation.recover_orphaned_jobs("wa")
    current = automation.list_jobs("wa")[0]
    assert recovered == {"requeued": 1, "reconciled": 0, "uncertain": 0}
    assert current["status"] == "queued"
    assert current["started_at"] is None


def test_restart_recovery_reconciles_already_sent_reply():
    job = incoming()[0]
    outbox = shadow.queue_message(
        "wa", "A", "Resposta já enviada", source="automation",
        idempotency_key="reply:" + job["id"],
    )
    shadow.complete_outbox(outbox["id"], transport_ref="ack-before-restart")
    with storage.connect() as conn:
        conn.execute(
            "UPDATE whatsapp_inbound_jobs SET status='processing',started_at=? WHERE id=?",
            ("2026-09-18T10:00:00+00:00", job["id"]),
        )
    recovered = automation.recover_orphaned_jobs("wa")
    current = automation.list_jobs("wa")[0]
    assert recovered == {"requeued": 0, "reconciled": 1, "uncertain": 0}
    assert current["status"] == "completed"
    assert current["outbox_id"] == outbox["id"]


def test_outgoing_never_creates_reply_job():
    shadow.update_conversation("wa","A",automation_mode="autonomous")
    shadow.ingest_messages("wa","A",[{"external_id":"out1","content":"oi","direction":"outgoing"}])
    assert automation.list_jobs("wa")==[]

def test_burst_has_one_canonical_run_and_one_outbox(monkeypatch):
    from meuharness import execution_service
    incoming(ident="in1")
    incoming(ident="in2")
    calls=[]
    async def execute(agent_id,prompt,**kwargs):
        calls.append(kwargs)
        assert kwargs["source"]=="automation"
        assert kwargs["whatsapp_contact"]=="A"
        key="reply:"+kwargs["whatsapp_delivery_key"]
        queued=shadow.queue_message("wa","A","Resposta",source="automation",idempotency_key=key)
        shadow.complete_outbox(queued["id"],transport_ref="verified")
        return SimpleNamespace(run_id="run-test")
    monkeypatch.setattr(execution_service,"execute_agent",execute)
    result=automation.process_inbound_once("wa",debounce_seconds=0)
    assert result["processed"]==2 and result["ok"]
    assert len(calls)==1
    assert all(j["status"]=="completed" for j in automation.list_jobs("wa"))
    assert automation.process_inbound_once("wa",debounce_seconds=0)["processed"]==0
    assert len(calls)==1

def test_provider_failure_after_verified_send_reconciles_job(monkeypatch):
    from meuharness import execution_service
    incoming()
    async def execute(agent_id,prompt,**kwargs):
        key="reply:"+kwargs["whatsapp_delivery_key"]
        queued=shadow.queue_message("wa","A","Resposta confirmada",source="automation",idempotency_key=key)
        shadow.complete_outbox(queued["id"],transport_ref="ack-real")
        exc=RuntimeError("provider failed after tool")
        exc.actis_run_id="run-provider-failed"
        raise exc
    monkeypatch.setattr(execution_service,"execute_agent",execute)
    result=automation.process_inbound_once("wa",debounce_seconds=0)
    job=automation.list_jobs("wa")[0]
    assert result["ok"] is True
    assert result["reason"]=="delivery_confirmed_before_agent_failure"
    assert job["status"]=="completed"
    assert job["run_id"]=="run-provider-failed"
    assert job["outbox_id"]

def test_handoff_after_enqueue_prevents_execution(monkeypatch):
    from meuharness import execution_service
    incoming()
    shadow.handoff_to_human("wa","A")
    async def forbidden(*a,**k):
        pytest.fail("paused contact triggered execution")
    monkeypatch.setattr(execution_service,"execute_agent",forbidden)
    result=automation.process_inbound_once("wa",debounce_seconds=0)
    assert result["ok"] is False
    assert automation.list_jobs("wa")[0]["status"]=="blocked"

def test_automatic_tools_cannot_read_or_write_another_contact():
    shadow.ingest_messages("wa","B",[{"external_id":"secret","content":"private","direction":"incoming"}],emit_inbound_events=False)
    tools={t.__name__:t for t in build_whatsapp_tools("wa",source="automation",contact_scope="A",delivery_key="job")}
    for name,args in [("whatsapp_local_recent",("B",)),
                      ("whatsapp_send_message",("B","text")),
                      ("whatsapp_handoff",("B",))]:
        assert json.loads(tools[name](*args))["reason"]=="contact_outside_current_request"
    assert json.loads(tools["whatsapp_local_recent"]())==[]
    assert all(x["contact_name"]=="A" for x in json.loads(tools["whatsapp_inbox"]()))

def test_automatic_context_never_includes_other_contacts():
    for contact in ["A","B"]:
        shadow.ingest_messages("wa",contact,[{"external_id":contact,"content":contact,"direction":"incoming"}],emit_inbound_events=False)
    package=shadow.shadow_context("wa",contact_name="A")
    assert package["monitored_contacts"]==["A"]
    assert all(x["contact"]=="A" for x in package["recent_messages"])
    assert all(x["contact"]=="A" for x in package["inbox"])

def test_automatic_runtime_has_no_raw_browser_or_admin_tools():
    from meuharness.execution_service import _runtime_tools
    tools=_runtime_tools("wa",{"whatsapp.local"},["harness_browser","actis_admin"],
        {"browser.interact","actis.admin"},None,"test","","automation","A","job")
    assert all(t.__name__.startswith("whatsapp_") for t in tools)

def test_job_cannot_send_multiple_distinct_responses():
    tools={t.__name__:t for t in build_whatsapp_tools("wa",source="automation",contact_scope="A",delivery_key="job")}
    tools["whatsapp_queue_message"]("A","first")
    with pytest.raises(ValueError,match="idempot"):
        tools["whatsapp_queue_message"]("A","second")
    assert len(shadow.pending_outbox("wa"))==1
