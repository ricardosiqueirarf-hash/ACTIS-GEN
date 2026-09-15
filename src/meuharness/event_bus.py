"""Durable SQLite event stream for ACTIS GEN observability."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from meuharness.storage import DATA_DIR, append_event, query_events

LEGACY_EVENTS_FILE = DATA_DIR / "events.jsonl"
_MIGRATED = False

def _migrate_legacy_events() -> None:
    global _MIGRATED
    if _MIGRATED or not LEGACY_EVENTS_FILE.is_file():
        _MIGRATED = True
        return
    for line in LEGACY_EVENTS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("id") and event.get("type"):
            append_event(event)
    _MIGRATED = True


def emit_event(event_type: str, **payload: Any) -> dict[str, Any]:
    _migrate_legacy_events()
    event_type = str(event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")
    event = {
        "id": uuid4().hex,
        "type": event_type,
        "created_at": datetime.now(UTC).isoformat(),
        **payload,
    }
    append_event(event)
    return event


def list_events(*, limit: int = 200, event_type: str | None = None,
                run_id: str | None = None) -> list[dict[str, Any]]:
    _migrate_legacy_events()
    return query_events(limit=limit, event_type=event_type, run_id=run_id)
