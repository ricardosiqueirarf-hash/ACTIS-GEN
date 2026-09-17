"""Deterministic WhatsApp Web outbox dispatcher.

The LLM requests a send; this runtime owns transport, retry and ACK verification.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from websockets.sync.client import connect

from meuharness import storage
from meuharness.browser_manager import ensure_browser
from meuharness.domains.whatsapp_shadow import (
    block_outbox,
    claim_outbox_id,
    complete_outbox,
    fail_outbox,
    outbox_item,
    pending_outbox,
    runtime_settings,
    sync_state_for_contact,
    uncertain_outbox,
)
from meuharness.domains.whatsapp_transport import (
    MESSAGE_SCAN_JS,
    BrowserBusyError,
    browser_lease,
    normalize_phone,
    phone_aliases,
)
from meuharness.event_bus import emit_event


def _targets(cdp_url: str) -> list[dict[str, Any]]:
    with urlopen(cdp_url + "/json/list", timeout=2) as response:
        value = json.load(response)
    return value if isinstance(value, list) else []


def _page(agent_id: str) -> dict[str, Any] | None:
    runtime = ensure_browser(agent_id)
    pages = [row for row in _targets(runtime.cdp_url) if row.get("type") == "page"]
    matches = [row for row in pages if urlparse(str(row.get("url") or "")).hostname == "web.whatsapp.com"]
    if matches:
        return matches[0]
    blank = next((row for row in pages if row.get("url") == "about:blank"), None)
    if blank:
        _cdp(blank, "Page.navigate", {"url": "https://web.whatsapp.com/"})
        page = blank
    else:
        request = Request(runtime.cdp_url + "/json/new?" + quote("https://web.whatsapp.com/", safe=""), method="PUT")
        with urlopen(request, timeout=5) as response:
            page = json.load(response)
    _wait_until(lambda: _authenticated(page), timeout=20)
    return page


def _cdp(page: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError("Aba do WhatsApp sem websocket CDP")
    with connect(ws_url, open_timeout=3, close_timeout=1) as websocket:
        websocket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            message = json.loads(websocket.recv(timeout=8))
            if message.get("id") != 1:
                continue
            if message.get("error"):
                raise RuntimeError(str(message["error"]))
            return dict(message.get("result") or {})


def _eval(page: dict[str, Any], expression: str) -> Any:
    result = _cdp(page, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    value = (result.get("result") or {})
    if value.get("subtype") == "error":
        raise RuntimeError(str(value.get("description") or "Erro JS"))
    return value.get("value")


def _authenticated(page: dict[str, Any]) -> bool:
    return bool(_eval(page, "Boolean(document.querySelector('#pane-side'))"))


def _composer_ready(page: dict[str, Any]) -> bool:
    script = """Boolean(document.querySelector('#main footer [contenteditable=\"true\"][role=\"textbox\"], #main footer [contenteditable=\"true\"]'))"""
    return bool(_eval(page, script))


def _wait_until(predicate, timeout: float = 25.0, interval: float = 0.35) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (RuntimeError, OSError, ValueError):
            pass
        time.sleep(interval)
    return False


def _phone(external_id: str | None, fallback: str) -> str:
    return normalize_phone(external_id or fallback)


def _verify_recipient(page: dict[str, Any], phone: str) -> bool:
    # Only visible contact-profile fields; never search message text for a number.
    script = r"""(() => [...document.querySelectorAll('section [data-testid="selectable-text"]')]
      .filter(e=>!e.closest('#main,#pane-side')&&e.getClientRects().length)
      .map(e=>(e.innerText||'').trim()).filter(s=>/^\+?[\d\s().-]{10,}$/.test(s)))()"""
    def matched():
        values = _eval(page, script) or []
        return any(normalize_phone(value) in phone_aliases(phone) for value in values)
    if matched():
        return True
    _eval(page, """(() => {const el=document.querySelector('#main header [data-testid="conversation-info-header"],#main header [title="Dados do perfil"]');if(!el)return false;el.click();return true;})()""")
    return _wait_until(matched, timeout=8)


def _open_chat(page: dict[str, Any], contact_name: str, external_id: str | None) -> None:
    phone = _phone(external_id, contact_name)
    if phone:
        _cdp(page, "Page.navigate", {"url": f"https://web.whatsapp.com/send?phone={phone}"})
        if not _wait_until(lambda: _composer_ready(page), timeout=30):
            raise RuntimeError("Conversa não carregou; verifique número e conexão")
        if not _verify_recipient(page, phone):
            raise RuntimeError("Número do destinatário não confirmado nos dados do contato; envio bloqueado")
        return
    encoded = json.dumps(contact_name, ensure_ascii=False)
    clicked = _eval(page, f"""(() => {{
      const wanted={encoded}.trim().toLowerCase();
      const hits=[...new Set([...document.querySelectorAll('#pane-side [title]')]
        .filter(el=>(el.getAttribute('title')||'').trim().toLowerCase()===wanted)
        .map(el=>el.closest('[role="row"], [tabindex]')||el))];
      if(hits.length!==1) return false;
      hits[0].dispatchEvent(new MouseEvent('mousedown',{{bubbles:true}}));hits[0].click();return true;
    }})()""")
    if not clicked:
        raise RuntimeError("Contato ausente ou ambíguo na lista; configure um telefone verificado")
    def selected():
        header = _eval(page, "document.querySelector('#main header [data-testid=conversation-info-header]')?.innerText||document.querySelector('#main header')?.innerText||''")
        return str(header).split("\n", 1)[0].strip().casefold() == contact_name.strip().casefold() and _composer_ready(page)
    if not _wait_until(selected, timeout=15):
        raise RuntimeError("Cabeçalho da conversa não corresponde ao contato solicitado")


def _matching_outgoing(page: dict[str, Any], content: str, *, require_ack: bool = False) -> list[str]:
    rows = _eval(page, MESSAGE_SCAN_JS) or []
    norm = lambda value: " ".join(str(value).split())
    return [str(row["external_id"]) for row in rows
            if row.get("direction") == "outgoing" and norm(row.get("content", "")) == norm(content)
            and (not require_ack or row.get("transport_ack"))]


def _matching_outgoing_document(page: dict[str, Any], filename: str, *, require_ack: bool = False) -> list[str]:
    rows = _eval(page, MESSAGE_SCAN_JS) or []
    wanted = Path(str(filename or "")).name.casefold()
    return [str(row["external_id"]) for row in rows
            if row.get("direction") == "outgoing" and wanted and wanted in str(row.get("content") or "").casefold()
            and (not require_ack or row.get("transport_ack"))]


def _document_payload(item: dict[str, Any]) -> tuple[Path, str, str]:
    try:
        payload = json.loads(str(item.get("payload") or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Payload do documento inválido") from exc
    path = Path(str(payload.get("path") or "")).expanduser().resolve()
    root = storage.DATA_DIR.expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Documento fora do DATA_DIR autorizado") from exc
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise RuntimeError("PDF da outbox não encontrado")
    expected = str(payload.get("sha256") or "")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and actual != expected:
        raise RuntimeError("PDF da outbox mudou depois de ser enfileirado")
    filename = Path(str(payload.get("filename") or path.name)).name
    caption = str(payload.get("caption") or "").strip()
    return path, filename, caption


def _stage_document(page: dict[str, Any], path: Path, filename: str) -> None:
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError("Aba do WhatsApp sem websocket CDP")
    with connect(ws_url, open_timeout=3, close_timeout=1) as websocket:
        counter = 0

        def send_request(method: str, params: dict[str, Any] | None = None) -> int:
            nonlocal counter
            counter += 1
            websocket.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            return counter

        def rpc(method: str, params: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
            request_id = send_request(method, params)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = json.loads(websocket.recv(timeout=max(0.1, deadline - time.monotonic())))
                except TimeoutError:
                    break
                if message.get("id") != request_id:
                    continue
                if message.get("error"):
                    raise RuntimeError(str(message["error"]))
                return dict(message.get("result") or {})
            raise TimeoutError(f"CDP sem resposta para {method}")

        def js(expression: str) -> Any:
            result = rpc("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
            value = result.get("result") or {}
            if value.get("subtype") == "error":
                raise RuntimeError(str(value.get("description") or "Erro JS"))
            return value.get("value")

        def document_input_object_id() -> str:
            expression = r"""(() => {
              const inputs=[...document.querySelectorAll('input[type=file]')];
              const doc=inputs.find(e=>{
                const a=(e.accept||'').trim().toLowerCase();
                return !a || a==='*' || a==='*/*' || a.includes('application/pdf') ||
                  (!a.includes('image')&&!a.includes('video')&&!a.includes('audio'));
              });
              return doc||null;
            })()"""
            result = rpc("Runtime.evaluate", {"expression": expression, "returnByValue": False})
            value = result.get("result") or {}
            return str(value.get("objectId") or "")

        def notify_file_input(object_id: str) -> None:
            rpc("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": "function(){this.dispatchEvent(new Event('change',{bubbles:true}));}",
                "returnByValue": True,
            })

        def inject_existing_document_input() -> bool:
            object_id = document_input_object_id()
            if not object_id:
                return False
            rpc("DOM.setFileInputFiles", {"objectId": object_id, "files": [str(path)]})
            notify_file_input(object_id)
            return True

        stage_method = "none"
        stage_detail = ""
        has_document_menu = bool(js("Boolean(document.querySelector('[role=menuitem][aria-label=\"Documento\"],[role=menuitem][aria-label=\"Document\"]'))"))
        if not has_document_menu:
            clicked = js("""(() => {
              const el=document.querySelector('#main footer [aria-label="Anexar"],#main footer [aria-label="Attach"]');
              if(!el)return false;el.click();return true;
            })()""")
            if not clicked:
                raise RuntimeError("Botão Anexar não encontrado")
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if js("Boolean(document.querySelector('[role=menuitem][aria-label=\"Documento\"],[role=menuitem][aria-label=\"Document\"]'))"):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Opção Documento não apareceu")

        # Page.fileChooserOpened is delivered per CDP session. Enable the Page/DOM
        # domains on this websocket before turning interception on; otherwise modern
        # Chromium can show the chooser without emitting the event to this client.
        rpc("Page.enable", {"enableFileChooserOpenedEvent": True})
        rpc("DOM.enable")
        rpc("Page.setInterceptFileChooserDialog", {"enabled": True})
        try:
            request_id = send_request("Runtime.evaluate", {
                "expression": """(() => {
                  const el=document.querySelector('[role=menuitem][aria-label="Documento"],[role=menuitem][aria-label="Document"]');
                  if(!el)return false;el.click();return true;
                })()""", "returnByValue": True, "userGesture": True,
            })
            chooser = None
            click_result = None
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and (chooser is None or click_result is None):
                try:
                    message = json.loads(websocket.recv(timeout=max(0.1, deadline - time.monotonic())))
                except TimeoutError:
                    break
                if message.get("id") == request_id:
                    if message.get("error"):
                        raise RuntimeError(str(message["error"]))
                    click_result = ((message.get("result") or {}).get("result") or {}).get("value")
                elif message.get("method") == "Page.fileChooserOpened":
                    chooser = dict(message.get("params") or {})
            if click_result is False:
                raise RuntimeError("Opção Documento não pôde ser acionada")
            if chooser and chooser.get("backendNodeId"):
                backend_node_id = int(chooser["backendNodeId"])
                resolved = rpc("DOM.resolveNode", {"backendNodeId": backend_node_id})
                object_id = str((resolved.get("object") or {}).get("objectId") or "")
                if object_id:
                    rpc("Runtime.callFunctionOn", {
                        "objectId": object_id,
                        "functionDeclaration": "function(){if(!this.isConnected){const root=document.querySelector('#app')||document.body;this.style.display='none';root.appendChild(this);}return this.isConnected;}",
                        "returnByValue": True,
                    })
                params = {"backendNodeId": backend_node_id, "files": [str(path)]}
                if object_id:
                    params["objectId"] = object_id
                rpc("DOM.setFileInputFiles", params)
                stage_method = "fileChooserOpened"
                if object_id:
                    check = rpc("Runtime.callFunctionOn", {
                        "objectId": object_id,
                        "functionDeclaration": "function(){return {count:this.files?this.files.length:0,connected:this.isConnected,accept:this.accept||''};}",
                        "returnByValue": True,
                    })
                    stage_detail = json.dumps(((check.get("result") or {}).get("value") or {}), ensure_ascii=False)
            elif inject_existing_document_input():
                stage_method = "hiddenInputFallback"
                # Fallback for WhatsApp builds that keep a hidden document input but
                # do not expose backendNodeId with the chooser event.
                pass
            else:
                raise RuntimeError("Seletor de arquivo do WhatsApp não foi interceptado nem localizado no DOM")
        finally:
            try:
                rpc("Page.setInterceptFileChooserDialog", {"enabled": False}, timeout=2)
            except Exception:
                pass

    expected = json.dumps(filename, ensure_ascii=False)
    def preview_ready() -> bool:
        return bool(_eval(page, f"""(() => {{
          const wanted={expected};
          const text=document.body.innerText||'';
          const send=[...document.querySelectorAll('button,[role=button]')].find(e=>e.getClientRects().length &&
            (/^(Enviar|Send)$/i.test((e.getAttribute('aria-label')||e.innerText||'').trim()) || /^(send|send-button)$/i.test(e.getAttribute('data-testid')||'')));
          return !!send && text.includes(wanted);
        }})()"""))
    if not _wait_until(preview_ready, timeout=12, interval=0.25):
        raise RuntimeError(f"Prévia do PDF não ficou pronta para envio (staging={stage_method}, detail={stage_detail})")


def _send_document_and_verify(page: dict[str, Any], item: dict[str, Any]) -> str:
    path, filename, _caption = _document_payload(item)
    before_ids = set(_matching_outgoing_document(page, filename))
    _stage_document(page, path, filename)
    clicked = False
    try:
        clicked = bool(_eval(page, """(() => {
          const buttons=[...document.querySelectorAll('button,[role=button]')].filter(e=>e.getClientRects().length);
          const el=buttons.find(e=>/^(Enviar|Send)$/i.test((e.getAttribute('aria-label')||e.innerText||'').trim()))
            ||buttons.find(e=>/^(send|send-button)$/i.test(e.getAttribute('data-testid')||''));
          if(!el)return false;el.click();return true;
        })()"""))
        if not clicked:
            raise RuntimeError("Botão de envio da prévia não encontrado")
        new_found: list[str] = []
        def verified() -> bool:
            nonlocal new_found
            new_found = [ref for ref in _matching_outgoing_document(page, filename, require_ack=True) if ref not in before_ids]
            return bool(new_found)
        if not _wait_until(verified, timeout=24, interval=0.45):
            raise RuntimeError("ACK do documento não observado")
        return new_found[-1]
    except Exception as exc:
        if clicked:
            raise AmbiguousDeliveryError("Documento pode ter sido enviado; repetição automática bloqueada: " + str(exc)) from exc
        raise


def _composer_text(page: dict[str, Any]) -> str:
    return str(_eval(page, "document.querySelector('#main footer [contenteditable=true]')?.innerText||''") or "")


def _focus_composer(page: dict[str, Any]) -> None:
    ok = _eval(page, """(() => {
      const el=document.querySelector('#main footer [contenteditable="true"][role="textbox"], #main footer [contenteditable="true"]');
      if(!el) return false;
      el.focus();
      return document.activeElement===el || el.contains(document.activeElement);
    })()""")
    if not ok:
        raise RuntimeError("Não foi possível focar o compositor principal")



class AmbiguousDeliveryError(RuntimeError):
    """Enter was dispatched, but the transport ACK could not be proven. Do not retry."""


def _send_and_verify(page: dict[str, Any], content: str) -> str:
    before_ids = set(_matching_outgoing(page, content))
    _focus_composer(page)
    if _composer_text(page).strip():
        raise RuntimeError("Há um rascunho no compositor; conteúdo preservado, envio bloqueado")
    _cdp(page, "Input.insertText", {"text": content})
    new_found: list[str] = []
    try:
        # From this point any error is ambiguous: Enter may already have reached WhatsApp.
        _cdp(page, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
        _cdp(page, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
        def verified() -> bool:
            nonlocal new_found
            new_found = [ref for ref in _matching_outgoing(page, content, require_ack=True) if ref not in before_ids]
            return bool(new_found)
        if not _wait_until(verified, timeout=18, interval=0.4):
            raise RuntimeError("ACK de envio não observado")
    except Exception as exc:
        raise AmbiguousDeliveryError("Envio pode ter ocorrido; repetição automática bloqueada: " + str(exc)) from exc
    return new_found[-1]


def _delivery_allowed(item: dict[str, Any]) -> tuple[bool, str]:
    mode = str(item.get("conversation_automation_mode") or "supervised")
    source = str(item.get("origin_source") or "user")
    automatic = source in {"automation", "automation_condition", "delegation"}
    if mode == "disabled":
        return False, "automation_disabled"
    if automatic and not bool(runtime_settings(str(item.get("agent_id") or ""))["automation_enabled"]):
        return False, "global_kill_switch"
    if automatic and mode != "autonomous":
        return False, f"automation_mode:{mode}"
    if automatic:
        state = sync_state_for_contact(str(item.get("agent_id") or ""), str(item.get("contact_name") or ""))
        if state and state.get("gap_suspected"):
            return False, "context_needs_verification"
    return True, ""


def _dispatch_locked(agent_id: str, outbox_id: str, max_attempts: int) -> dict[str, Any]:
    existing = outbox_item(outbox_id)
    if not existing or existing.get("agent_id") != agent_id:
        raise ValueError("Outbox item não encontrado para este agente")
    if existing["status"] not in {"pending", "failed"}:
        return existing
    allowed, reason = _delivery_allowed(existing)
    if not allowed:
        return block_outbox(outbox_id, reason) or existing
    try:
        page = _page(agent_id)
        if not page or not _authenticated(page):
            return {**existing, "ok": False, "reason": "whatsapp_not_authenticated"}
    except Exception as exc:  # noqa: BLE001 - transport boundary; preserve queue before claiming
        return {**existing, "ok": False, "reason": "transport_unavailable", "error": str(exc)[:300]}
    item = claim_outbox_id(outbox_id, max_attempts=max_attempts)
    if not item or not item.pop("_claimed", False):
        return item or existing
    emit_event("whatsapp.outbox.sending", agent_id=agent_id, outbox_id=outbox_id,
               conversation_id=item.get("conversation_id"), contact=item.get("contact_name"))
    transport_ref = None
    try:
        _open_chat(page, str(item.get("contact_name") or ""), item.get("contact_external_id"))
        allowed, reason = _delivery_allowed(outbox_item(outbox_id) or item)
        if not allowed:
            return block_outbox(outbox_id, reason) or item
        if str(item.get("message_type") or "text") == "document":
            transport_ref = _send_document_and_verify(page, item)
        else:
            transport_ref = _send_and_verify(page, str(item.get("content") or ""))
        return complete_outbox(outbox_id, transport_ref=transport_ref) or item
    except AmbiguousDeliveryError as exc:
        return uncertain_outbox(outbox_id, str(exc)) or item
    except Exception as exc:  # noqa: BLE001 - never retry a confirmed external side effect
        if transport_ref:
            return uncertain_outbox(outbox_id, "ACK recebido; falha ao persistir: " + str(exc)) or item
        return fail_outbox(outbox_id, str(exc), max_attempts=max_attempts) or item


def dispatch_outbox_item(agent_id: str, outbox_id: str, *, max_attempts: int = 3) -> dict[str, Any]:
    item = outbox_item(outbox_id)
    if not item or item.get("agent_id") != agent_id:
        raise ValueError("Outbox item não encontrado para este agente")
    try:
        with browser_lease(agent_id, wait_seconds=8):
            return _dispatch_locked(agent_id, outbox_id, max_attempts)
    except BrowserBusyError:
        return {**item, "ok": False, "reason": "browser_busy"}


def dispatch_once(agent_id: str, *, limit: int = 3, max_attempts: int = 3) -> list[dict[str, Any]]:
    try:
        with browser_lease(agent_id):
            items = [row for row in pending_outbox(agent_id, limit=100)
                     if row["status"] in {"pending", "failed"} and row["attempts"] < max_attempts
                     and row.get("origin_source") in {"automation", "automation_condition"}]
            return [_dispatch_locked(agent_id, row["id"], max_attempts)
                    for row in items[:max(1, min(limit, 20))]]
    except BrowserBusyError:
        return []


def open_session(agent_id: str) -> dict[str, Any]:
    try:
        with browser_lease(agent_id, wait_seconds=8):
            page = _page(agent_id)
            connected = bool(page and _authenticated(page))
            return {"ok": connected, "connected": connected,
                    "reason": "connected" if connected else "authentication_required"}
    except BrowserBusyError:
        return {"ok": False, "reason": "browser_busy"}


def send_message(agent_id: str, contact_name: str, content: str, *, source: str = "user",
                 idempotency_key: str | None = None) -> dict[str, Any]:
    from meuharness.domains.whatsapp_shadow import queue_message
    queued = queue_message(agent_id, contact_name, content, source=source, idempotency_key=idempotency_key)
    if queued.get("status") in {"sent", "uncertain", "sending", "blocked", "dead"}:
        return queued
    return dispatch_outbox_item(agent_id, str(queued["id"]))


def send_document(agent_id: str, contact_name: str, path: str, *, filename: str = "", caption: str = "",
                  source: str = "user", idempotency_key: str | None = None) -> dict[str, Any]:
    from meuharness.domains.whatsapp_shadow import queue_document
    queued = queue_document(agent_id, contact_name, path, filename=filename, caption=caption,
                            source=source, idempotency_key=idempotency_key)
    if queued.get("status") in {"sent", "uncertain", "sending", "blocked", "dead"}:
        return queued
    return dispatch_outbox_item(agent_id, str(queued["id"]))
