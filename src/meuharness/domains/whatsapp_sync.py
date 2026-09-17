"""Incremental WhatsApp Web -> local shadow synchronizer.

It reuses the managed browser profile/tab of the WhatsApp agent. It never opens a
second WhatsApp tab, which preserves the authenticated session selected by ACTIS.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any
from urllib.request import urlopen

from websockets.sync.client import connect

from meuharness.agents import list_agents, list_runs
from meuharness.domains.whatsapp_dispatcher import _open_chat, _page, dispatch_once
from meuharness.domains.whatsapp_shadow import (
    get_contact,
    ingest_messages,
    known_external_ids,
    list_monitored_contacts,
    set_sync_continuity,
    sync_state_for_contact,
)
from meuharness.domains.whatsapp_transport import MESSAGE_SCAN_JS, BrowserBusyError, browser_lease
from meuharness.skills import has_skill

LOGGER = logging.getLogger("meuharness.whatsapp_sync")


def _agent_busy(agent_id: str, current_run_id: str = "") -> bool:
    return any(str(run.get("status") or "") == "running" and (not current_run_id or run.get("id") != current_run_id) for run in list_runs(agent_id))


def _targets(cdp_url: str) -> list[dict[str, Any]]:
    with urlopen(cdp_url + "/json/list", timeout=2) as response:
        value = json.load(response)
    return value if isinstance(value, list) else []


def _whatsapp_page(agent_id: str) -> dict[str, Any] | None:
    return _page(agent_id)


def _eval(page: dict[str, Any], expression: str) -> Any:
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError("Aba do WhatsApp sem websocket CDP")
    with connect(ws_url, open_timeout=3, close_timeout=1) as websocket:
        websocket.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }}))
        while True:
            message = json.loads(websocket.recv(timeout=6))
            if message.get("id") != 1:
                continue
            if message.get("error"):
                raise RuntimeError(str(message["error"]))
            result = ((message.get("result") or {}).get("result") or {})
            if result.get("subtype") == "error":
                raise RuntimeError(str(result.get("description") or "Erro JS"))
            return result.get("value")


def _authenticated(page: dict[str, Any]) -> bool:
    script = """(() => Boolean(document.querySelector('#pane-side')))()"""
    return bool(_eval(page, script))


def _open_contact(page: dict[str, Any], name: str, external_id: str | None = None) -> bool:
    try:
        _open_chat(page, name, external_id)
        return True
    except (RuntimeError, OSError, ValueError):
        return False


def discover_sidebar_contacts(agent_id: str, *, limit: int = 80) -> dict[str, Any]:
    """Return the currently loaded WhatsApp sidebar conversations without mutating the UI."""
    page = _whatsapp_page(agent_id)
    if not page:
        return {"ok": False, "reason": "whatsapp_tab_not_open", "contacts": []}
    if not _authenticated(page):
        return {"ok": False, "reason": "whatsapp_not_authenticated", "contacts": []}
    script = r"""(() => [...document.querySelectorAll('#pane-side [role="row"]')]
      .map(row => {
        const title=row.querySelector('[title]')?.getAttribute('title')||'';
        const text=(row.innerText||'').trim();
        return title ? {name:title, preview:text.split('\n').slice(1).join(' · ').slice(0,180)} : null;
      }).filter(Boolean))()"""
    value = _eval(page, script)
    rows = value if isinstance(value, list) else []
    seen: set[str] = set()
    contacts: list[dict[str, str]] = []
    for row in rows:
        name = str((row or {}).get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        contacts.append({"name": name, "preview": str((row or {}).get("preview") or "")})
        if len(contacts) >= max(1, min(int(limit), 200)):
            break
    return {"ok": True, "contacts": contacts}


def _scrape_messages(page: dict[str, Any]) -> list[dict[str, Any]]:
    value = _eval(page, MESSAGE_SCAN_JS)
    return value if isinstance(value, list) else []


def _message_ids(messages: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("external_id") or "") for row in messages if row.get("external_id")}


def _merge_messages(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for row in [*existing, *incoming]:
        external_id = str(row.get("external_id") or "").strip()
        if external_id:
            merged[external_id] = row
        else:
            key = (str(row.get("timestamp") or ""), str(row.get("direction") or ""), str(row.get("content") or ""))
            if not any((str(x.get("timestamp") or ""), str(x.get("direction") or ""), str(x.get("content") or "")) == key for x in anonymous):
                anonymous.append(row)
    return [*merged.values(), *anonymous]


def _scroll_older(page: dict[str, Any]) -> dict[str, Any]:
    script = r"""(() => {
      const first=document.querySelector('#main [data-id]');
      if(!first) return {moved:false,at_top:false};
      let el=first.parentElement;
      while(el && el!==document.body){
        if(el.scrollHeight > el.clientHeight + 40){
          const before=el.scrollTop;
          el.scrollTop=Math.max(0,before-Math.max(500,el.clientHeight*0.82));
          return {moved:el.scrollTop!==before,at_top:el.scrollTop<=2,before,after:el.scrollTop};
        }
        el=el.parentElement;
      }
      return {moved:false,at_top:false};
    })()"""
    value = _eval(page, script)
    return value if isinstance(value, dict) else {"moved": False, "at_top": False}


def _collect_until_anchor(page: dict[str, Any], known_ids: set[str], *, rounds: int = 10) -> dict[str, Any]:
    messages = _scrape_messages(page)
    overlap = _message_ids(messages) & known_ids
    if overlap:
        return {"messages": messages, "anchor": next(iter(overlap)), "overlap_count": len(overlap), "at_top": False, "exhausted": False}
    at_top = False
    for _ in range(max(1, rounds)):
        scroll = _scroll_older(page)
        at_top = bool(scroll.get("at_top"))
        if not scroll.get("moved") and not at_top:
            time.sleep(0.25)
        else:
            time.sleep(0.55)
        messages = _merge_messages(messages, _scrape_messages(page))
        overlap = _message_ids(messages) & known_ids
        if overlap:
            return {"messages": messages, "anchor": next(iter(overlap)), "overlap_count": len(overlap), "at_top": at_top, "exhausted": False}
        if at_top:
            break
    return {"messages": messages, "anchor": None, "overlap_count": 0, "at_top": at_top, "exhausted": True}


def _extend_backfill(page: dict[str, Any], messages: list[dict[str, Any]], *, rounds: int = 4) -> tuple[list[dict[str, Any]], bool]:
    at_top = False
    for _ in range(max(1, rounds)):
        scroll = _scroll_older(page)
        at_top = bool(scroll.get("at_top"))
        time.sleep(0.5)
        messages = _merge_messages(messages, _scrape_messages(page))
        if at_top:
            break
    return messages, at_top


def _sync_locked(agent_id: str, *, contact_name: str | None = None, current_run_id: str = "",
                 emit_inbound_events: bool = True) -> dict[str, Any]:
    if _agent_busy(agent_id, current_run_id):
        return {"ok": False, "reason": "agent_busy", "synced": 0}
    page = _whatsapp_page(agent_id)
    if not page:
        return {"ok": False, "reason": "whatsapp_tab_not_open", "synced": 0}
    if not _authenticated(page):
        return {"ok": False, "reason": "whatsapp_not_authenticated", "synced": 0}
    synced = 0
    contacts = list_monitored_contacts(agent_id)
    if contact_name:
        contact = get_contact(agent_id, contact_name)
        if not contact:
            return {"ok": False, "reason": "contact_not_found", "synced": 0}
        contacts = [contact]
    results: list[dict[str, Any]] = []
    for contact in contacts:
        if _agent_busy(agent_id, current_run_id):
            return {"ok": False, "reason": "agent_busy", "synced": synced, "contacts": results}
        name = str(contact.get("name") or "")
        if not name:
            continue
        if not _open_contact(page, name, contact.get("external_id")):
            results.append({"contact": name, "ok": False, "reason": "contact_not_verified"})
            continue
        time.sleep(0.45)
        state = sync_state_for_contact(agent_id, name)
        known = set(known_external_ids(agent_id, name))
        initial_sync = not state or not known
        if initial_sync:
            messages = _scrape_messages(page)
            at_top = False
            if bool(contact.get("sync_history")):
                messages, at_top = _extend_backfill(page, messages, rounds=10)
            elif len(messages) > 40:
                messages = messages[-40:]
            result = ingest_messages(agent_id, name, messages, emit_inbound_events=False, verified_contact=True)
            status = "complete" if at_top and bool(contact.get("sync_history")) else ("backfilling" if contact.get("sync_history") else "healthy")
            continuity = set_sync_continuity(
                agent_id, name, status=status, overlap_count=0,
                gap_suspected=False, backfill_complete=bool(at_top and contact.get("sync_history")),
                last_complete_scan=bool(at_top and contact.get("sync_history")),
            )
        else:
            scan = _collect_until_anchor(page, known, rounds=10)
            messages = list(scan["messages"])
            anchor = scan.get("anchor")
            overlap_count = int(scan.get("overlap_count") or 0)
            at_top = bool(scan.get("at_top"))
            if not anchor:
                status = "gap_suspected" if at_top else "backfilling"
                result = ingest_messages(agent_id, name, messages, emit_inbound_events=emit_inbound_events, verified_contact=True)
                synced += int(result.get("inserted") or 0)
                continuity = set_sync_continuity(
                    agent_id, name, status=status, overlap_count=0,
                    gap_suspected=bool(at_top), error="known_anchor_not_found",
                )
                results.append({
                    "contact": name, "ok": False, "reason": status,
                    "inserted": result.get("inserted", 0), "continuity": continuity,
                })
                continue
            backfill_complete = bool(state.get("backfill_complete"))
            if bool(contact.get("sync_history")) and not backfill_complete:
                messages, reached_top = _extend_backfill(page, messages, rounds=4)
                backfill_complete = reached_top
            result = ingest_messages(agent_id, name, messages, emit_inbound_events=emit_inbound_events, verified_contact=True)
            status = "complete" if backfill_complete else ("backfilling" if contact.get("sync_history") else "healthy")
            continuity = set_sync_continuity(
                agent_id, name, status=status, anchor_external_id=str(anchor),
                overlap_count=overlap_count, gap_suspected=False,
                backfill_complete=backfill_complete,
                last_complete_scan=backfill_complete,
            )
        synced += int(result.get("inserted") or 0)
        results.append({
            "contact": name, "ok": True, "inserted": result.get("inserted", 0),
            "status": continuity.get("status"), "anchor": continuity.get("anchor_external_id"),
            "overlap_count": continuity.get("overlap_count", 0),
            "gap_suspected": bool(continuity.get("gap_suspected")),
            "backfill_complete": bool(continuity.get("backfill_complete")),
        })
    return {"ok": all(row.get("ok") for row in results), "synced": synced, "contacts": results}


def sync_once(agent_id: str, *, contact_name: str | None = None, current_run_id: str = "",
              emit_inbound_events: bool = True) -> dict[str, Any]:
    if _agent_busy(agent_id, current_run_id):
        return {"ok": False, "reason": "agent_busy", "synced": 0}
    try:
        with browser_lease(agent_id, wait_seconds=8 if contact_name else 0):
            return _sync_locked(agent_id, contact_name=contact_name, current_run_id=current_run_id,
                                emit_inbound_events=emit_inbound_events)
    except BrowserBusyError:
        return {"ok": False, "reason": "browser_busy", "synced": 0}


def whatsapp_agent_ids() -> list[str]:
    return [
        str(agent.get("id"))
        for agent in list_agents()
        if agent.get("id") and has_skill(list(agent.get("skills") or []), "browser.whatsapp")
    ]


def loop(agent_id: str | None = None, poll_seconds: float = 5.0) -> None:
    LOGGER.info("WhatsApp sync started%s", f" for {agent_id}" if agent_id else " for all skilled agents")
    while True:
        targets = [agent_id] if agent_id else whatsapp_agent_ids()
        if not targets:
            LOGGER.warning("No agents with browser.whatsapp skill are configured")
        for target in targets:
            try:
                if _agent_busy(target):
                    LOGGER.info("WhatsApp runtime [%s]: skipped because agent is busy", target)
                    continue
                result = sync_once(target)
                dispatched = dispatch_once(target, limit=3) if result.get("reason") != "agent_busy" else []
                from meuharness.domains.whatsapp_automation import process_inbound_once
                inbound = process_inbound_once(target)
                if inbound.get("processed"):
                    LOGGER.info("WhatsApp inbound [%s]: %s", target, json.dumps(inbound, ensure_ascii=False))
                LOGGER.info(
                    "WhatsApp runtime [%s]: sync=%s dispatch=%s",
                    target, json.dumps(result, ensure_ascii=False),
                    json.dumps(dispatched, ensure_ascii=False),
                )
            except KeyboardInterrupt:
                raise
            except Exception:
                LOGGER.exception("WhatsApp sync iteration failed for %s", target)
        time.sleep(max(1.0, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="ACTIS GEN WhatsApp shadow synchronizer")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    agent_id = args.agent_id.strip() or None
    if args.once:
        targets = [agent_id] if agent_id else whatsapp_agent_ids()
        print(json.dumps({
            target: {"sync": sync_once(target), "dispatch": dispatch_once(target, limit=3)}
            for target in targets
        }, ensure_ascii=False))
        return
    loop(agent_id, args.poll_seconds)


if __name__ == "__main__":
    main()
