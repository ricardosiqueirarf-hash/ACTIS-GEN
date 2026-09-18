from types import SimpleNamespace

import pytest

from meuharness import storage
from meuharness.domains import whatsapp_automation as automation
from meuharness.domains import whatsapp_shadow as shadow


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(storage, 'DB_FILE', tmp_path / 'actis.db')
    monkeypatch.setenv('ACTIS_WHATSAPP_OPENCLAW_SHADOW_ENABLED', '0')
    from meuharness import agents
    monkeypatch.setattr(agents, 'list_runs', lambda _: [])


def test_internal_completion_marker_never_enters_outbox():
    item = shadow.queue_message('wa', 'Cliente', 'Tudo certo!\n<CPA_DONE>', source='automation')
    assert item['content'] == 'Tudo certo!'


def test_simple_model_text_is_delivered_when_model_does_not_call_send_tool(monkeypatch):
    from meuharness import execution_service
    from meuharness.domains import whatsapp_dispatcher

    shadow.update_conversation('wa', 'Cliente', automation_mode='autonomous')
    shadow.ingest_messages(
        'wa', 'Cliente',
        [{'external_id':'m-simple','direction':'incoming','content':'Como está?'}],
        verified_contact=True,
    )
    shadow.set_sync_continuity('wa', 'Cliente', status='healthy')

    async def fake_execute(*args, **kwargs):
        return SimpleNamespace(run_id='run-simple', text='Tudo certo por aqui! <CPA_DONE>')
    monkeypatch.setattr(execution_service, 'execute_agent', fake_execute)

    def fake_send(agent_id, contact, content, **kwargs):
        queued = shadow.queue_message(
            agent_id, contact, content, source=kwargs.get('source','automation'),
            idempotency_key=kwargs.get('idempotency_key'),
        )
        return shadow.complete_outbox(queued['id'], transport_ref='test:ack')
    monkeypatch.setattr(whatsapp_dispatcher, 'send_message', fake_send)

    result = automation.process_inbound_once('wa', debounce_seconds=0)
    assert result['ok'] is True
    # sent rows are not pending, so inspect by idempotency key directly
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM whatsapp_outbox WHERE idempotency_key LIKE 'reply:%'").fetchone()
    assert row is not None
    assert row['status'] == 'sent'
    assert row['content'] == 'Tudo certo por aqui!'
