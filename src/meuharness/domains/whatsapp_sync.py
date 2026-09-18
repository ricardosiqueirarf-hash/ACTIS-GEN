"""Incremental WhatsApp Web -> local shadow synchronizer.

It reuses the managed browser profile/tab of the WhatsApp agent. It never opens a
second WhatsApp tab, which preserves the authenticated session selected by ACTIS.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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
from meuharness.domains.whatsapp_transport import MESSAGE_SCAN_JS, BrowserBusyError, browser_lease, normalize_phone, phone_aliases
from meuharness.skills import has_skill

LOGGER = logging.getLogger("meuharness.whatsapp_sync")

# Broken/unverifiable contacts must not stall every poll cycle. Manual/explicit
# syncs still bypass this cache; it only protects the background full scan.
_CONTACT_BACKOFF_UNTIL: dict[tuple[str, str], float] = {}
_CONTACT_BACKOFF_SECONDS = 300.0


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


def _sidebar_preview_for_contact(contact: dict[str, Any], rows: dict[str, str]) -> tuple[str, str] | None:
    """Resolve a monitored contact against the currently loaded sidebar without opening it."""
    wanted_name = str(contact.get("name") or "").strip()
    wanted_phone = normalize_phone(str(contact.get("external_id") or wanted_name))
    for title, preview in rows.items():
        if title.strip().casefold() == wanted_name.casefold():
            return title, preview
        title_phone = normalize_phone(title)
        if wanted_phone and title_phone and title_phone in phone_aliases(wanted_phone):
            return title, preview
    return None


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
    fast_contact = os.environ.get("ACTIS_WHATSAPP_FAST_CONTACT", "").strip()
    if not contact_name and fast_contact:
        # Keep the fast chat as the final successful chat of a background scan,
        # so the next fast poll can reuse the already verified conversation.
        contacts = sorted(contacts, key=lambda row: str(row.get("name") or "") == fast_contact)
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
        backoff_key = (agent_id, name)
        if contact_name is None and time.monotonic() < _CONTACT_BACKOFF_UNTIL.get(backoff_key, 0.0):
            results.append({"contact": name, "ok": True, "skipped": True, "reason": "contact_backoff"})
            continue
        if not _open_contact(page, name, contact.get("external_id")):
            if contact_name is None:
                _CONTACT_BACKOFF_UNTIL[backoff_key] = time.monotonic() + _CONTACT_BACKOFF_SECONDS
            results.append({"contact": name, "ok": False, "reason": "contact_not_verified"})
            continue
        _CONTACT_BACKOFF_UNTIL.pop(backoff_key, None)
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


def loop(agent_id: str | None = None, poll_seconds: float = 5.0, *,
         fast_contact: str = "", full_scan_seconds: float = 60.0) -> None:
    fast_contact = fast_contact.strip()
    if fast_contact:
        os.environ["ACTIS_WHATSAPP_FAST_CONTACT"] = fast_contact
    LOGGER.info(
        "WhatsApp sync started%s%s",
        f" for {agent_id}" if agent_id else " for all skilled agents",
        f" fast_contact={fast_contact}" if fast_contact else "",
    )
    last_sidebar_check: dict[str, float] = {}
    sidebar_cache: dict[str, dict[str, str]] = {}
    recovered_targets: set[str] = set()
    while True:
        targets = [agent_id] if agent_id else whatsapp_agent_ids()
        if not targets:
            LOGGER.warning("No agents with browser.whatsapp skill are configured")
        for target in targets:
            try:
                if target not in recovered_targets:
                    from meuharness.domains.whatsapp_automation import recover_orphaned_jobs
                    recovered = recover_orphaned_jobs(target)
                    recovered_targets.add(target)
                    if any(recovered.values()):
                        LOGGER.warning("WhatsApp restart recovery [%s]: %s", target, json.dumps(recovered))
                if _agent_busy(target):
                    LOGGER.info("WhatsApp runtime [%s]: skipped because agent is busy", target)
                    continue
                from meuharness.domains.whatsapp_automation import process_inbound_once
                from meuharness.domains.whatsapp_baileys import sync_once as sync_baileys_once
                from meuharness.domains.whatsapp_channel_gateway import process_shadow_once as _process_shadow_once
                shadow_inline = str(os.getenv("ACTIS_WHATSAPP_SHADOW_INLINE", "0")).strip().lower() in {"1", "true", "yes", "on"}
                if shadow_inline:
                    process_shadow_once = _process_shadow_once
                else:
                    def process_shadow_once(*_args, **_kwargs):
                        return {"ok": True, "processed": 0, "reason": "shadow_out_of_band"}

                primary_result = sync_baileys_once(target)
                if primary_result.get("ok"):
                    dispatched = dispatch_once(target, limit=3)
                    inbound_fast = (
                        process_inbound_once(target, debounce_seconds=0.0, contact_name=fast_contact)
                        if fast_contact else {"ok": True, "processed": 0}
                    )
                    inbound = inbound_fast
                    if not inbound_fast.get("processed"):
                        inbound = process_inbound_once(target, debounce_seconds=0.75)
                    shadow_fast = (
                        process_shadow_once(target, peer_id=fast_contact)
                        if fast_contact else {"ok": True, "processed": 0}
                    )
                    shadow_result = shadow_fast
                    if not shadow_fast.get("processed"):
                        shadow_result = process_shadow_once(target)
                    if inbound.get("processed"):
                        LOGGER.info(
                            "WhatsApp Baileys inbound [%s]: %s",
                            target, json.dumps(inbound, ensure_ascii=False),
                        )
                    if shadow_result.get("processed"):
                        LOGGER.info(
                            "WhatsApp shadow [%s]: %s",
                            target, json.dumps(shadow_result, ensure_ascii=False),
                        )
                    LOGGER.info(
                        "WhatsApp runtime [%s]: transport=baileys sync=%s dispatch=%s",
                        target, json.dumps(primary_result, ensure_ascii=False),
                        json.dumps(dispatched, ensure_ascii=False),
                    )
                    continue

                if fast_contact:
                    # Fast lane: sync only the test/operator contact and process it
                    # immediately, with no five-second burst debounce.
                    result = sync_once(target, contact_name=fast_contact)
                    dispatched = dispatch_once(target, limit=3) if result.get("reason") != "agent_busy" else []
                    inbound = process_inbound_once(
                        target, debounce_seconds=0.0, contact_name=fast_contact
                    )
                    shadow_result = process_shadow_once(target, peer_id=fast_contact)
                    if shadow_result.get("processed"):
                        LOGGER.info(
                            "WhatsApp shadow [%s/%s]: %s",
                            target, fast_contact, json.dumps(shadow_result, ensure_ascii=False),
                        )
                    if inbound.get("processed"):
                        LOGGER.info(
                            "WhatsApp fast inbound [%s/%s]: %s",
                            target, fast_contact, json.dumps(inbound, ensure_ascii=False),
                        )

                    # Background chats are discovered by cheap sidebar preview changes.
                    # We never run a blind multi-contact scan in front of the fast lane.
                    now = time.monotonic()
                    last_sidebar_check.setdefault(target, 0.0)
                    background_result = None
                    background_inbound = None
                    restored_fast = None
                    if (
                        not inbound.get("processed")
                        and now - last_sidebar_check[target] >= max(2.0, full_scan_seconds)
                    ):
                        sidebar = discover_sidebar_contacts(target, limit=100)
                        current_rows = {
                            str(row.get("name") or ""): str(row.get("preview") or "")
                            for row in (sidebar.get("contacts") or [])
                            if row.get("name")
                        } if sidebar.get("ok") else {}
                        previous_rows = sidebar_cache.get(target)
                        if previous_rows is not None and current_rows:
                            for contact in list_monitored_contacts(target):
                                name = str(contact.get("name") or "")
                                if not name or name == fast_contact:
                                    continue
                                match = _sidebar_preview_for_contact(contact, current_rows)
                                if not match:
                                    continue
                                title, preview = match
                                if previous_rows.get(title) == preview:
                                    continue
                                backoff_key = (target, name)
                                if time.monotonic() < _CONTACT_BACKOFF_UNTIL.get(backoff_key, 0.0):
                                    continue
                                background_result = sync_once(target, contact_name=name)
                                if background_result.get("ok"):
                                    _CONTACT_BACKOFF_UNTIL.pop(backoff_key, None)
                                    background_inbound = process_inbound_once(
                                        target, debounce_seconds=1.0, contact_name=name
                                    )
                                    background_shadow = process_shadow_once(target, peer_id=name)
                                    if background_shadow.get("processed"):
                                        LOGGER.info(
                                            "WhatsApp shadow [%s/%s]: %s",
                                            target, name, json.dumps(background_shadow, ensure_ascii=False),
                                        )
                                else:
                                    _CONTACT_BACKOFF_UNTIL[backoff_key] = (
                                        time.monotonic() + _CONTACT_BACKOFF_SECONDS
                                    )
                                # Any background sync changed the active chat. Restore the
                                # verified fast chat before the next loop.
                                restored_fast = sync_once(target, contact_name=fast_contact)
                                break
                        if current_rows:
                            sidebar_cache[target] = current_rows
                        last_sidebar_check[target] = time.monotonic()
                    LOGGER.info(
                        "WhatsApp runtime [%s]: fast_sync=%s dispatch=%s background_sync=%s background_inbound=%s restored_fast=%s",
                        target,
                        json.dumps(result, ensure_ascii=False),
                        json.dumps(dispatched, ensure_ascii=False),
                        json.dumps(background_result, ensure_ascii=False) if background_result is not None else "-",
                        json.dumps(background_inbound, ensure_ascii=False) if background_inbound is not None else "-",
                        json.dumps(restored_fast, ensure_ascii=False) if restored_fast is not None else "-",
                    )
                else:
                    result = sync_once(target)
                    dispatched = dispatch_once(target, limit=3) if result.get("reason") != "agent_busy" else []
                    inbound = process_inbound_once(target)
                    shadow_result = process_shadow_once(target)
                    if shadow_result.get("processed"):
                        LOGGER.info("WhatsApp shadow [%s]: %s", target, json.dumps(shadow_result, ensure_ascii=False))
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
        time.sleep(max(0.5, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="ACTIS GEN WhatsApp shadow synchronizer")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fast-contact", default="")
    parser.add_argument("--full-scan-seconds", type=float, default=60.0)
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
    loop(agent_id, args.poll_seconds, fast_contact=args.fast_contact, full_scan_seconds=args.full_scan_seconds)


if __name__ == "__main__":
    main()
