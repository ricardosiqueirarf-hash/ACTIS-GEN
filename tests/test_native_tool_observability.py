from __future__ import annotations

import asyncio
import inspect

from meuharness.adapters.maf import observed_tool as observed


def _patch_runtime(monkeypatch):
    events = []
    monkeypatch.setattr(observed, "set_run_activity", lambda *a, **k: None)
    monkeypatch.setattr(observed, "update_work_by_run", lambda *a, **k: None)
    monkeypatch.setattr(observed, "emit_event", lambda name, **data: events.append((name, data)))
    return events


def test_native_sync_tool_emits_live_events_and_trace(monkeypatch):
    events = _patch_runtime(monkeypatch)
    observed.clear_run_tool_trace("run-sync")

    def sample(value: int) -> int:
        """sample doc"""
        return value + 1

    wrapped = observed.observe_native_tool(sample, run_id="run-sync", agent_id="worker")
    assert inspect.signature(wrapped) == inspect.signature(sample)
    assert wrapped(4) == 5
    assert [name for name, _ in events] == ["tool.started", "tool.completed"]
    assert observed.get_run_tool_trace("run-sync") == [{"tool": "sample", "ok": True}]


def test_native_async_tool_failure_emits_failed_and_trace(monkeypatch):
    events = _patch_runtime(monkeypatch)
    observed.clear_run_tool_trace("run-async")

    async def sample_async() -> str:
        raise RuntimeError("boom")

    wrapped = observed.observe_native_tool(sample_async, run_id="run-async", agent_id="worker")
    try:
        asyncio.run(wrapped())
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")
    assert [name for name, _ in events] == ["tool.started", "tool.failed"]
    assert observed.get_run_tool_trace("run-async") == [{"tool": "sample_async", "ok": False}]
