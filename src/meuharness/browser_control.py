"""Human inspection/control of each agent's managed Browser Harness Chrome."""
from __future__ import annotations

import json
from typing import Any
from urllib.request import urlopen

from websockets.sync.client import connect

from meuharness.browser_manager import ensure_browser


def _targets(cdp_url: str) -> list[dict[str, Any]]:
    with urlopen(cdp_url + "/json/list", timeout=2) as response:
        value = json.load(response)
    return value if isinstance(value, list) else []


def _page(agent_id: str) -> tuple[Any, dict[str, Any]]:
    runtime = ensure_browser(agent_id)
    pages = [item for item in _targets(runtime.cdp_url) if item.get("type") == "page"]
    if not pages:
        raise RuntimeError("Browser do agente não possui nenhuma aba aberta.")
    page = pages[0]
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError("A aba atual não expôs websocket CDP.")
    return runtime, page


def _cdp(ws_url: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with connect(ws_url, open_timeout=3, close_timeout=1) as websocket:
        websocket.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            message = json.loads(websocket.recv(timeout=5))
            if message.get("id") != 1:
                continue
            if message.get("error"):
                raise RuntimeError(str(message["error"].get("message") or message["error"]))
            return dict(message.get("result") or {})


def snapshot(agent_id: str) -> dict[str, Any]:
    runtime, page = _page(agent_id)
    ws_url = str(page["webSocketDebuggerUrl"])
    metrics = _cdp(
        ws_url,
        "Runtime.evaluate",
        {"expression": "JSON.stringify({w:innerWidth,h:innerHeight,dpr:devicePixelRatio,title:document.title,url:location.href})", "returnByValue": True},
    )
    raw = (((metrics.get("result") or {}).get("value")) or "{}")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        info = {}
    shot = _cdp(ws_url, "Page.captureScreenshot", {"format": "jpeg", "quality": 72, "fromSurface": True})
    image = str(shot.get("data") or "")
    return {
        "agent_id": runtime.agent_id,
        "pid": runtime.pid,
        "port": runtime.port,
        "profile_dir": runtime.profile_dir,
        "title": str(info.get("title") or page.get("title") or ""),
        "url": str(info.get("url") or page.get("url") or ""),
        "width": int(info.get("w") or 1440),
        "height": int(info.get("h") or 1000),
        "image": "data:image/jpeg;base64," + image if image else "",
    }


def action(agent_id: str, action_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    _runtime, page = _page(agent_id)
    ws_url = str(page["webSocketDebuggerUrl"])
    action_name = str(action_name or "").strip().lower()
    if action_name == "navigate":
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("URL vazia.")
        if "://" not in url and not url.startswith(("about:", "data:", "file:", "chrome:")):
            url = "https://" + url
        _cdp(ws_url, "Page.navigate", {"url": url})
    elif action_name == "reload":
        _cdp(ws_url, "Page.reload")
    elif action_name == "click":
        x, y = float(payload.get("x") or 0), float(payload.get("y") or 0)
        _cdp(ws_url, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        _cdp(ws_url, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
    elif action_name == "type":
        text = str(payload.get("text") or "")
        _cdp(ws_url, "Input.insertText", {"text": text})
    elif action_name == "key":
        key = str(payload.get("key") or "Enter")
        code = "Enter" if key.lower() in {"enter", "return"} else key
        _cdp(ws_url, "Input.dispatchKeyEvent", {"type": "keyDown", "key": code, "code": code})
        _cdp(ws_url, "Input.dispatchKeyEvent", {"type": "keyUp", "key": code, "code": code})
    else:
        raise ValueError("Ação de browser desconhecida.")
    return {"ok": True, "action": action_name}
