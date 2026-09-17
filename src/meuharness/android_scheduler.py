"""Automation scheduler for the embedded Android Core."""
from __future__ import annotations

import asyncio
import threading

from meuharness.android_execution import execute_agent_android
from meuharness.automations import claim_due_automations, record_condition, record_result, release_claim
from meuharness.event_bus import emit_event


def _run_action(automation: dict) -> None:
    automation_id = str(automation.get("id") or "")
    try:
        result = asyncio.run(execute_agent_android(str(automation["agent_id"]), str(automation.get("action") or ""), source="automation"))
        record_result(automation_id, status="completed", response=result.text, run_id=result.run_id)
        emit_event("automation.completed", automation_id=automation_id, run_id=result.run_id, runtime="android-embedded")
    except Exception as exc:
        record_result(automation_id, status="failed", error=str(exc))
        emit_event("automation.failed", automation_id=automation_id, error_type=type(exc).__name__, runtime="android-embedded")


def _check_condition(automation: dict) -> None:
    automation_id = str(automation.get("id") or "")
    prompt = (
        "Avalie a condição abaixo. NÃO execute a ação. Responda na primeira linha exatamente "
        "ACTIS_TRIGGER se verdadeira agora, ou ACTIS_WAIT se falsa. Na segunda linha explique.\n\nCONDIÇÃO: "
        + str(automation.get("condition") or "")
    )
    try:
        result = asyncio.run(execute_agent_android("general", prompt, source="automation_condition"))
        text = result.text.strip()
        active = (text.splitlines() or [""])[0].strip().upper().startswith("ACTIS_TRIGGER")
        updated, trigger = record_condition(automation_id, active, text)
        if trigger and updated:
            _run_action(updated)
    except Exception as exc:
        release_claim(automation_id, str(exc))
        emit_event("automation.condition_failed", automation_id=automation_id, error_type=type(exc).__name__, runtime="android-embedded")


def _worker(automation: dict) -> None:
    if automation.get("trigger_type") == "condition":
        _check_condition(automation)
    else:
        _run_action(automation)


def scheduler_loop(stop_event: threading.Event, poll_seconds: float = 5.0) -> None:
    emit_event("scheduler.started", mode="android-embedded")
    while not stop_event.is_set():
        try:
            for automation in claim_due_automations():
                threading.Thread(target=_worker, args=(automation,), daemon=True, name=f"actis-android-auto-{str(automation.get('id'))[:6]}").start()
        except Exception as exc:
            emit_event("scheduler.failed", error_type=type(exc).__name__, mode="android-embedded")
        stop_event.wait(max(2.0, poll_seconds))
    emit_event("scheduler.stopped", mode="android-embedded")


def start_android_scheduler() -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    thread = threading.Thread(target=scheduler_loop, args=(stop,), daemon=True, name="actis-android-scheduler")
    thread.start()
    return stop, thread
