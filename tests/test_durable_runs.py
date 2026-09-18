from __future__ import annotations

import asyncio
from types import SimpleNamespace

from meuharness import durable_runs, execution_service, storage


def _use_tmp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)


def test_checkpoint_journal_is_ordered_and_idempotent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    first = durable_runs.append_checkpoint(
        "run-1", "agent-1", "tool", detail="send", idempotency_key="send:42"
    )
    repeated = durable_runs.append_checkpoint(
        "run-1", "agent-1", "tool", detail="send-again", idempotency_key="send:42"
    )
    second = durable_runs.append_checkpoint("run-1", "agent-1", "verify", detail="ack")

    assert first["id"] == repeated["id"]
    assert second["seq"] == 2
    assert [row["phase"] for row in durable_runs.list_checkpoints("run-1")] == ["tool", "verify"]


def test_resume_run_links_new_attempt(monkeypatch):
    monkeypatch.setattr(
        execution_service,
        "get_run",
        lambda run_id: {
            "id": run_id,
            "agent_id": "worker",
            "prompt": "faça a tarefa",
            "status": "interrupted",
            "response": "ação parcial",
            "conversation_id": "conv-1",
            "parent_agent_id": None,
        },
    )
    seen = {}

    async def fake_execute(agent_id, prompt, **kwargs):
        seen.update({"agent_id": agent_id, "prompt": prompt, **kwargs})
        return SimpleNamespace(run_id="run-2", text="ok", model="test/model", elapsed_ms=1.0, effective_tools=())

    monkeypatch.setattr(execution_service, "execute_agent", fake_execute)
    result = asyncio.run(execution_service.resume_run("run-1"))

    assert result.run_id == "run-2"
    assert seen["source"] == "resume"
    assert seen["resume_from_run_id"] == "run-1"
    assert seen["conversation_id"] == "conv-1"
