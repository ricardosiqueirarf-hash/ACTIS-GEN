#!/data/data/com.termux/files/usr/bin/python3
"""Low-latency WhatsApp Web control for ACTIS Browser Harness on Android.

The script talks directly to the persistent Chromium CDP endpoint. It avoids
starting the Browser Harness CLI/daemon for every simple WhatsApp action.
Sending requires an explicit --confirm-send flag.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(os.environ.get("ACTIS_GEN_ROOT", Path.home() / "ACTIS-GEN"))
sys.path.insert(0, str(ROOT / "src"))

from meuharness.browser_manager import ensure_browser  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

BROWSER_ID = os.environ.get("ACTIS_BROWSER_AGENT_ID", "chatgpt-android")
WA_URL = "https://web.whatsapp.com/"


class CDP:
    def __init__(self, ws_url: str):
        self.ws = connect(ws_url, open_timeout=2, close_timeout=0.2, max_size=None)
        self.seq = 0

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, method: str, params: dict | None = None, timeout: float = 2.0) -> dict:
        self.seq += 1
        ident = self.seq
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(self.ws.recv(timeout=max(0.05, deadline - time.monotonic())))
            if msg.get("id") != ident:
                continue
            if msg.get("error"):
                raise RuntimeError(msg["error"].get("message") or str(msg["error"]))
            return msg.get("result") or {}
        raise TimeoutError(f"CDP timeout: {method}")

    def js(self, expression: str):
        out = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        result = out.get("result") or {}
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description") or "JavaScript error")
        return result.get("value")

    def click(self, x: float, y: float) -> None:
        base = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
        self.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
        self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})

    def text(self, text: str) -> None:
        self.call("Input.insertText", {"text": text})

    def key(self, key: str, code: str | None = None, vk: int = 0, modifiers: int = 0) -> None:
        code = code or key
        base = {
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
            "modifiers": modifiers,
        }
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def clear_focused(self) -> None:
        self.key("a", "KeyA", 65, modifiers=2)
        self.key("Backspace", "Backspace", 8)


def targets(cdp_url: str) -> list[dict]:
    with urlopen(cdp_url + "/json/list", timeout=1) as r:
        data = json.load(r)
    return data if isinstance(data, list) else []


def whatsapp_target(cdp_url: str) -> dict:
    pages = [x for x in targets(cdp_url) if x.get("type") == "page"]
    for item in pages:
        if "web.whatsapp.com" in str(item.get("url") or ""):
            return item
    if not pages:
        raise RuntimeError("Chromium não possui aba utilizável.")
    item = pages[0]
    cdp = CDP(str(item["webSocketDebuggerUrl"]))
    try:
        cdp.call("Page.navigate", {"url": WA_URL}, timeout=2)
    finally:
        cdp.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for candidate in targets(cdp_url):
            if "web.whatsapp.com" in str(candidate.get("url") or ""):
                return candidate
        time.sleep(0.05)
    raise RuntimeError("WhatsApp Web não abriu.")


def wait_until(cdp: CDP, expression: str, timeout: float = 1.5, step: float = 0.035):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cdp.js(expression)
        if last:
            return last
        time.sleep(step)
    return last


def composer_state(cdp: CDP) -> dict | None:
    return cdp.js("""(()=>{const e=document.querySelector('#main [data-testid="conversation-compose-box-input"],#main [contenteditable="true"][role="textbox"]');if(!e)return null;const r=e.getBoundingClientRect();return {aria:e.getAttribute('aria-label')||'',text:e.innerText||'',x:r.left+r.width/2,y:r.top+r.height/2}})()""")


def open_chat(cdp: CDP, chat: str, timeout: float = 1.8) -> dict:
    wanted = json.dumps(chat)
    state = composer_state(cdp)
    if state and chat.casefold() in str(state.get("aria", "")).casefold():
        return {"ok": True, "chat": chat, "reused": True, "searched": False}

    rect = cdp.js(f"""(()=>{{const name={wanted};const els=[...document.querySelectorAll('[data-testid="chat-list"] [title]')].filter(e=>e.getAttribute('title')===name);if(!els.length)return null;const row=els[0].closest('[role="row"]');if(!row)return null;let r=row.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight){{row.scrollIntoView({{block:'center'}});r=row.getBoundingClientRect();}}return {{x:r.left+r.width/2,y:r.top+r.height/2}}}})()""")
    searched = False
    if not rect:
        searched = True
        search = cdp.js("""(()=>{const e=document.querySelector('input[data-tab="3"],input[aria-label="Search or start a new chat"],input[placeholder="Search or start a new chat"]');if(!e)return null;const r=e.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()""")
        if not search:
            raise RuntimeError("Busca do WhatsApp não encontrada.")
        cdp.click(search["x"], search["y"])
        cdp.clear_focused()
        cdp.text(chat)
        rect = wait_until(
            cdp,
            f"""(()=>{{const name={wanted};const els=[...document.querySelectorAll('[title]')].filter(e=>e.getAttribute('title')===name);for(const e of els){{const row=e.closest('[role="row"]');if(row){{let r=row.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight){{row.scrollIntoView({{block:'center'}});r=row.getBoundingClientRect();}}if(r.width&&r.height)return {{x:r.left+r.width/2,y:r.top+r.height/2}};}}}}return null}})()""",
            timeout=1.2,
        )
        if not rect:
            cdp.clear_focused()
            raise RuntimeError(f"Conversa não encontrada: {chat}")

    cdp.click(rect["x"], rect["y"])
    ready = wait_until(
        cdp,
        f"""(()=>{{const e=document.querySelector('#main [data-testid="conversation-compose-box-input"],#main [contenteditable="true"][role="textbox"]');if(!e)return null;const a=e.getAttribute('aria-label')||'';return a.toLowerCase().includes({json.dumps(chat.casefold())})?a:null}})()""",
        timeout=timeout,
    )
    if not ready:
        raise RuntimeError(f"WhatsApp não confirmou abertura de {chat}.")
    return {"ok": True, "chat": chat, "reused": False, "searched": searched, "composer": ready}


def clear_draft(cdp: CDP) -> dict:
    state = composer_state(cdp)
    if not state:
        raise RuntimeError("Nenhuma conversa aberta.")
    cdp.click(state["x"], state["y"])
    cdp.clear_focused()
    return {"ok": True, "cleared": True}


def draft(cdp: CDP, chat: str, text: str) -> dict:
    opened = open_chat(cdp, chat)
    state = composer_state(cdp)
    if not state:
        raise RuntimeError("Composer não encontrado.")
    cdp.click(state["x"], state["y"])
    cdp.clear_focused()
    cdp.text(text)
    value = wait_until(
        cdp,
        f"""(()=>{{const e=document.querySelector('#main [data-testid="conversation-compose-box-input"],#main [contenteditable="true"][role="textbox"]');return e&&(e.innerText||'').includes({json.dumps(text)})?(e.innerText||''):null}})()""",
        timeout=0.5,
    )
    if value is None:
        raise RuntimeError("Texto não apareceu no composer.")
    return {**opened, "drafted": True, "chars": len(text)}


def send(cdp: CDP, chat: str, text: str, confirmed: bool) -> dict:
    if not confirmed:
        raise RuntimeError("Envio bloqueado: use --confirm-send para uma autorização explícita.")
    result = draft(cdp, chat, text)
    cdp.key("Enter", "Enter", 13)
    emptied = wait_until(
        cdp,
        """(()=>{const e=document.querySelector('#main [data-testid="conversation-compose-box-input"],#main [contenteditable="true"][role="textbox"]');return e!==null && (e.innerText||'').trim()===''})()""",
        timeout=1.0,
    )
    if not emptied:
        raise RuntimeError("Estado incerto: Enter foi enviado, mas o composer não esvaziou.")
    return {**result, "sent": True}


def status(cdp: CDP) -> dict:
    info = cdp.js("""(()=>({title:document.title,url:location.href,loggedIn:!!document.querySelector('[data-testid="chat-list"]'),chat:(document.querySelector('#main [data-testid="conversation-compose-box-input"],#main [contenteditable="true"][role="textbox"]')?.getAttribute('aria-label')||'')}))()""")
    return info or {}


def main() -> int:
    parser = argparse.ArgumentParser(prog="chatgpt-wa")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("open"); p.add_argument("chat")
    p = sub.add_parser("draft"); p.add_argument("chat"); p.add_argument("text")
    sub.add_parser("clear")
    p = sub.add_parser("send"); p.add_argument("chat"); p.add_argument("text"); p.add_argument("--confirm-send", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    runtime = ensure_browser(BROWSER_ID)
    target = whatsapp_target(runtime.cdp_url)
    cdp = CDP(str(target["webSocketDebuggerUrl"]))
    try:
        if args.cmd == "status": out = status(cdp)
        elif args.cmd == "open": out = open_chat(cdp, args.chat)
        elif args.cmd == "draft": out = draft(cdp, args.chat, args.text)
        elif args.cmd == "clear": out = clear_draft(cdp)
        elif args.cmd == "send": out = send(cdp, args.chat, args.text, args.confirm_send)
        else: raise AssertionError(args.cmd)
    finally:
        cdp.close()
    out["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
