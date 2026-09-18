from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from meuharness import execution_service as service


def _patch_base(monkeypatch, *, harness):
    monkeypatch.setattr(
        service,
        "get_agent",
        lambda agent_id: {
            "id": agent_id,
            "model": "test/model",
            "instructions": "base",
            "tools": [],
        },
    )
    monkeypatch.setattr(
        service.NineRouterSettings,
        "from_env",
        lambda *args, **kwargs: SimpleNamespace(model="test/model", timeout_s=30),
    )
    monkeypatch.setattr(service, "_runtime_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "connector_runtime_catalog", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "append_checkpoint", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "create_harness", lambda *args, **kwargs: harness)
    monkeypatch.setattr(service, "start_run", lambda *args, **kwargs: {"id": "run-1"})


def test_execute_agent_emits_started_and_completed(monkeypatch):
    class Harness:
        async def run(self, request):
            return SimpleNamespace(text="ok", model="test/model", elapsed_ms=12.5)

    _patch_base(monkeypatch, harness=Harness())
    finished = []
    events = []
    monkeypatch.setattr(service, "finish_run", lambda run_id, **data: finished.append((run_id, data)))
    monkeypatch.setattr(service, "emit_event", lambda event_type, **data: events.append((event_type, data)))

    result = asyncio.run(service.execute_agent("general", "hello", source="direct"))

    assert result.run_id == "run-1"
    assert result.text == "ok"
    assert [name for name, _ in events] == ["run.started", "run.completed"]
    assert finished == [("run-1", {"response": "ok", "elapsed_ms": 12.5})]


def test_execute_agent_records_failure_and_exposes_run_id(monkeypatch):
    class Harness:
        async def run(self, request):
            raise RuntimeError("boom")

    _patch_base(monkeypatch, harness=Harness())
    finished = []
    events = []
    monkeypatch.setattr(service, "finish_run", lambda run_id, **data: finished.append((run_id, data)))
    monkeypatch.setattr(service, "emit_event", lambda event_type, **data: events.append((event_type, data)))

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(service.execute_agent("worker", "fail", source="automation"))

    assert getattr(exc_info.value, "actis_run_id", None) == "run-1"
    assert finished == [("run-1", {"error": "boom"})]
    assert [name for name, _ in events] == ["run.started", "run.failed"]


def test_cancel_active_run_cancels_real_async_task(monkeypatch):
    started = asyncio.Event()

    class Harness:
        async def run(self, request):
            started.set()
            await asyncio.Event().wait()

    _patch_base(monkeypatch, harness=Harness())
    cancelled = []
    events = []
    monkeypatch.setattr(service, "cancel_run_record", lambda run_id: cancelled.append(run_id) or True)
    monkeypatch.setattr(service, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "emit_event", lambda event_type, **data: events.append(event_type))

    async def scenario():
        task = asyncio.create_task(service.execute_agent("worker", "wait"))
        await started.wait()
        assert service.cancel_active_run("run-1") is True
        with pytest.raises(RuntimeError, match="cancelada pelo usuário"):
            await task

    asyncio.run(scenario())
    assert cancelled == ["run-1"]
    assert events == ["run.started", "run.cancelled"]


def test_browser_runs_receive_longer_deadline_and_heartbeat(monkeypatch):
    seen = {}

    class Harness:
        async def run(self, request):
            seen["timeout_s"] = request.timeout_s
            return SimpleNamespace(text="ok", model="test/model", elapsed_ms=12.5)

    monkeypatch.setattr(
        service,
        "get_agent",
        lambda agent_id: {
            "id": agent_id,
            "model": "test/model",
            "instructions": "base",
            "tools": ["harness_browser"],
        },
    )
    monkeypatch.setattr(
        service.NineRouterSettings,
        "from_env",
        lambda *args, **kwargs: SimpleNamespace(model="test/model", timeout_s=kwargs.get("timeout_s", 30)),
    )
    monkeypatch.setattr(service, "_runtime_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "create_harness", lambda *args, **kwargs: Harness())
    monkeypatch.setattr(service, "start_run", lambda *args, **kwargs: {"id": "run-browser"})
    monkeypatch.setattr(service, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "emit_event", lambda *args, **kwargs: None)

    result = asyncio.run(service.execute_agent("browser-agent", "wait for whatsapp"))

    assert result.text == "ok"
    assert seen["timeout_s"] == 300


def test_automation_and_delegation_receive_multistep_deadline(monkeypatch):
    seen = []

    class Harness:
        async def run(self, request):
            seen.append(request.timeout_s)
            return SimpleNamespace(text="ok", model="test/model", elapsed_ms=12.5)

    monkeypatch.setattr(
        service,
        "get_agent",
        lambda agent_id: {
            "id": agent_id,
            "model": "test/model",
            "instructions": "base",
            "tools": [],
        },
    )
    monkeypatch.setattr(
        service.NineRouterSettings,
        "from_env",
        lambda *args, **kwargs: SimpleNamespace(model="test/model", timeout_s=kwargs.get("timeout_s", 30)),
    )
    monkeypatch.setattr(service, "_runtime_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "create_harness", lambda *args, **kwargs: Harness())
    monkeypatch.setattr(service, "start_run", lambda *args, **kwargs: {"id": "run-bg"})
    monkeypatch.setattr(service, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "emit_event", lambda *args, **kwargs: None)

    assert asyncio.run(service.execute_agent("worker", "job", source="automation")).text == "ok"
    assert asyncio.run(service.execute_agent("worker", "job", source="delegation")).text == "ok"
    assert seen == [120, 120]


def test_browser_success_claim_requires_post_mutation_verification(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_run_tool_trace",
        lambda run_id: [
            {"tool": "browser_type", "ok": True},
            {"tool": "browser_press", "ok": True},
            {"tool": "browser_screenshot", "ok": True},
        ],
    )
    assert service._browser_success_is_verified("run-1", "A mensagem foi enviada.") is False


def test_browser_success_claim_accepts_deterministic_verification(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_run_tool_trace",
        lambda run_id: [
            {"tool": "browser_press", "ok": True},
            {"tool": "browser_js", "ok": True},
        ],
    )
    assert service._browser_success_is_verified("run-1", "A mensagem foi enviada.") is True


def test_browser_non_success_text_does_not_require_verification(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    assert service._browser_success_is_verified("run-1", "Não consegui confirmar o envio.") is True


def test_operational_ack_only_is_not_completion(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    for text in ("ok", "Certo.", "feito", "pronto!", "entendi"):
        assert service._response_is_unfinished_intent(
            "run-1", text, prompt="Atualize o cadastro do cliente"
        ) is True


def test_ack_only_is_allowed_for_non_operational_chat(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    assert service._response_is_unfinished_intent(
        "run-1", "ok", prompt="Obrigado pela explicação"
    ) is False


def test_tool_evidence_allows_short_operational_confirmation(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_run_tool_trace",
        lambda run_id: [{"tool": "browser_js", "ok": True}],
    )
    assert service._response_is_unfinished_intent(
        "run-1", "feito", prompt="Verifique o estado no navegador"
    ) is False


def test_future_action_narration_never_counts_as_completion_without_evidence(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    assert service._response_is_unfinished_intent(
        "run-1",
        "Vou consultar as automações do sistema para verificar se você tem alguma ativa.",
        prompt="eu tenho alguma automação ativa?",
    ) is True


def test_colorglass_native_action_counts_as_browser_action_from_durable_checkpoint(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    monkeypatch.setattr(
        service,
        "list_checkpoints",
        lambda run_id, limit=1000: [
            {"phase": "tool", "status": "completed", "detail": "colorglass_quote_add_door"}
        ],
    )
    assert service._has_browser_evidence("run-1") is True
    assert service._required_browser_action_missing(
        "run-1", "salve o orçamento", {"browser.required_for_action"}
    ) is False


def test_colorglass_native_action_is_self_verifying(monkeypatch):
    monkeypatch.setattr(service, "get_run_tool_trace", lambda run_id: [])
    monkeypatch.setattr(
        service,
        "list_checkpoints",
        lambda run_id, limit=1000: [
            {"phase": "tool", "status": "completed", "detail": "colorglass_quote_add_door"}
        ],
    )
    assert service._browser_success_is_verified("run-1", "Orçamento atualizado e salvo.") is True


def test_control_plane_action_requires_current_run_tool_evidence():
    from meuharness.adapters.maf.observed_tool import clear_run_tool_trace
    from meuharness.execution_service import _response_is_unfinished_intent
    run_id = "control-plane-no-evidence"
    clear_run_tool_trace(run_id)
    assert _response_is_unfinished_intent(
        run_id,
        "Verificação concluída e PDF gerado com sucesso.",
        prompt="verifique o pedido 510 e gere o PDF",
        require_action_tool=True,
    )


def test_explicit_company_task_requirement_must_have_tool_evidence(monkeypatch):
    prompt = "Gere o relatório e envie via company_request_task ao agente responsável."
    monkeypatch.setattr(
        service,
        "get_run_tool_trace",
        lambda run_id: [{"tool": "finance_daily_report", "ok": True}],
    )
    monkeypatch.setattr(service, "list_checkpoints", lambda *args, **kwargs: [])
    assert service._response_is_unfinished_intent(
        "run-1",
        "As fontes foram consultadas. Vou agora criar a tarefa de envio.",
        prompt=prompt,
    ) is True

    monkeypatch.setattr(
        service,
        "get_run_tool_trace",
        lambda run_id: [
            {"tool": "finance_daily_report", "ok": True},
            {"tool": "company_request_task", "ok": True},
        ],
    )
    assert service._response_is_unfinished_intent(
        "run-1",
        "Relatório entregue ao agente responsável.",
        prompt=prompt,
    ) is False
