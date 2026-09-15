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
