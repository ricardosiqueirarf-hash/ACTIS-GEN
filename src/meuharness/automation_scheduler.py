"""Independent background scheduler for ACTIS GEN automations."""
from __future__ import annotations

import argparse
import asyncio
import logging
import threading

from meuharness.agents import list_agents
from meuharness.automations import (
    claim_due_automations,
    record_condition,
    record_result,
    release_claim,
)
from meuharness.event_bus import emit_event
from meuharness.execution_service import execute_agent

LOGGER = logging.getLogger("meuharness.automation_scheduler")


def _condition_evaluator_agent_id() -> str:
    for agent in list_agents():
        if "actis.control-plane" in list(agent.get("skills") or []):
            return str(agent.get("id"))
    for agent in list_agents():
        if "actis_admin" in list(agent.get("tools") or []):
            return str(agent.get("id"))
    raise RuntimeError("Nenhum agente com skill actis.control-plane disponível para avaliar condição.")


def _run_action(automation: dict, env_file: str | None = None) -> None:
    automation_id = str(automation.get("id") or "")
    emit_event("automation.started", automation_id=automation_id, agent_id=automation.get("agent_id"), trigger_type=automation.get("trigger_type"))
    try:
        result = asyncio.run(execute_agent(str(automation["agent_id"]), str(automation.get("action") or ""), source="automation", env_file=env_file))
        record_result(automation_id, status="completed", response=result.text, run_id=result.run_id)
        emit_event("automation.completed", automation_id=automation_id, agent_id=automation.get("agent_id"), run_id=result.run_id)
    except Exception as exc:
        record_result(automation_id, status="failed", error=str(exc))
        emit_event("automation.failed", automation_id=automation_id, agent_id=automation.get("agent_id"), error_type=type(exc).__name__)
        LOGGER.exception("automation action failed: %s", automation_id)


def _check_condition(automation: dict, env_file: str | None = None) -> None:
    automation_id = str(automation.get("id") or "")
    prompt = (
        "Avalie a condição abaixo. Use tools somente se necessário. NÃO execute a ação. "
        "Responda na primeira linha exatamente ACTIS_TRIGGER se verdadeira agora, ou ACTIS_WAIT se falsa. "
        "Na segunda linha, explique em uma frase.\n\nCONDIÇÃO: " + str(automation.get("condition") or "")
    )
    try:
        result = asyncio.run(execute_agent(_condition_evaluator_agent_id(), prompt, source="automation_condition", env_file=env_file))
        text = result.text.strip()
        active = (text.splitlines() or [""])[0].strip().upper().startswith("ACTIS_TRIGGER")
        updated, trigger = record_condition(automation_id, active, text)
        emit_event("automation.condition_checked", automation_id=automation_id, active=active, triggered=trigger)
        if trigger and updated:
            _run_action(updated, env_file)
    except Exception as exc:
        release_claim(automation_id, str(exc))
        emit_event("automation.condition_failed", automation_id=automation_id, error_type=type(exc).__name__)
        LOGGER.exception("automation condition failed: %s", automation_id)


def _worker(automation: dict, env_file: str | None) -> None:
    if automation.get("trigger_type") == "condition":
        _check_condition(automation, env_file)
    else:
        _run_action(automation, env_file)


def scheduler_loop(stop_event: threading.Event, poll_seconds: float = 5.0, env_file: str | None = None) -> None:
    emit_event("scheduler.started", mode="independent")
    while not stop_event.is_set():
        try:
            for automation in claim_due_automations():
                threading.Thread(target=_worker, args=(automation, env_file), daemon=True, name=f"actis-auto-{str(automation.get('id'))[:6]}").start()
        except Exception as exc:
            emit_event("scheduler.failed", error_type=type(exc).__name__)
            LOGGER.exception("automation scheduler poll failed")
        stop_event.wait(poll_seconds)
    emit_event("scheduler.stopped", mode="independent")


def start_scheduler(_base_url: str | None = None, *, env_file: str | None = None) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    thread = threading.Thread(target=scheduler_loop, args=(stop, 5.0, env_file), daemon=True, name="actis-automation-scheduler")
    thread.start()
    return stop, thread


def main() -> None:
    parser = argparse.ArgumentParser(description="ACTIS GEN independent automation scheduler")
    parser.add_argument("--env-file")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    stop = threading.Event()
    try:
        scheduler_loop(stop, max(1.0, args.poll_seconds), args.env_file)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
