"""Isolated ChatGPT Browser MCP proof-of-concept for ACTIS.

This module lives outside ``src/`` on purpose. It reuses ACTIS' browser
manager only to obtain a dedicated Chrome profile and never registers itself
with ACTIS agents, tools, routes, or production runtime.
"""
from __future__ import annotations

import functools
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from meuharness.browser_manager import ensure_browser  # noqa: E402

AGENT_ID = os.getenv("ACTIS_CHATGPT_BROWSER_ID", "chatgpt-poc")
RUNTIME = ensure_browser(AGENT_ID)
PROFILE = Path(RUNTIME.profile_dir)
os.environ["BU_CDP_URL"] = RUNTIME.cdp_url
os.environ["BU_NAME"] = "chatgpt-poc"
os.environ["BH_RUNTIME_DIR"] = str(PROFILE / "chatgpt-mcp-runtime")
os.environ["BH_TMP_DIR"] = str(PROFILE / "chatgpt-mcp-tmp")
os.environ["ACTIS_BROWSER_PROFILE"] = RUNTIME.profile_dir
os.environ["ACTIS_BROWSER_AGENT_ID"] = RUNTIME.agent_id

from mcp.server import MCPServer  # noqa: E402
from browser_harness.admin import ensure_daemon  # noqa: E402
from browser_harness.helpers import (  # noqa: E402
    capture_screenshot,
    click_at_xy,
    close_tab,
    current_tab,
    fill_input,
    goto_url,
    js,
    list_tabs,
    page_info,
    press_key,
    scroll,
    switch_tab,
    type_text,
    wait,
)

SERVER = MCPServer("actis-chatgpt-browser-poc")
AUDIT_DIR = Path.home() / ".local" / "share" / "actis-chatgpt-browser-poc"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"
REF_RE = re.compile(r"^@?e(\d{1,4})$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def _redact_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in {"text", "password", "content"}:
            safe[key] = f"<redacted:{len(str(value))} chars>"
        else:
            safe[key] = value
    return safe


def _audit(name: str, kwargs: dict[str, Any], *, ok: bool, error: str = "") -> None:
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "tool": name,
        "args": _redact_args(kwargs),
        "ok": ok,
    }
    if error:
        row["error"] = error[:500]
    with AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(_json(row) + "\n")


@contextmanager
def _stdout_to_stderr():
    saved = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = saved


def _tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            ensure_daemon()
            with _stdout_to_stderr():
                result = fn(*args, **kwargs)
            _audit(fn.__name__, kwargs, ok=True)
            return _json(result)
        except Exception as exc:  # noqa: BLE001 - MCP boundary serializes failures.
            _audit(fn.__name__, kwargs, ok=False, error=str(exc))
            return _json({"error": str(exc), "tool": fn.__name__})

    return SERVER.tool(name=fn.__name__, description=fn.__doc__ or "")(wrapper)
@_tool
def browser_status():
    """Return the isolated ChatGPT browser runtime identity and current page."""
    info = page_info()
    return {
        "agent_id": RUNTIME.agent_id,
        "profile_dir": RUNTIME.profile_dir,
        "cdp_url": RUNTIME.cdp_url,
        "pid": RUNTIME.pid,
        "page": info,
    }


@_tool
def browser_goto(url: str):
    """Navigate the current isolated tab to an http(s) URL or about:blank."""
    value = str(url or "").strip()
    if value == "about:blank":
        return goto_url(value)
    if not value.startswith(("http://", "https://")):
        raise ValueError("POC only allows http://, https:// or about:blank navigation.")
    return goto_url(value)


@_tool
def browser_page_info():
    """Return current URL, title, viewport and scroll information."""
    return page_info()


@_tool
def browser_screenshot(path: str | None = None, full: bool = False):
    """Capture a screenshot of the isolated browser and return its local path."""
    target = capture_screenshot(path=path, full=full, max_dim=1800)
    return {"path": str(target)}
SNAPSHOT_JS = r"""
(() => {
  const maxItems = __MAX_ITEMS__;
  const query = 'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]';
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const items = [];
  for (const el of document.querySelectorAll(query)) {
    if (!visible(el)) continue;
    const ref = `e${items.length + 1}`;
    el.setAttribute('data-actis-chatgpt-ref', ref);
    const r = el.getBoundingClientRect();
    const label = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '')
      .trim().replace(/\s+/g, ' ').slice(0, 220);
    items.push({ref: `@${ref}`, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', type: el.getAttribute('type') || '', label,
      bbox: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}});
    if (items.length >= maxItems) break;
  }
  const bodyText = (document.body?.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 6000);
  return {url: location.href, title: document.title, text: bodyText, elements: items};
})()
"""
@_tool
def browser_snapshot(max_elements: int = 120):
    """Return compact visible text plus stable temporary refs for interactive elements."""
    limit = max(1, min(int(max_elements), 300))
    return js(SNAPSHOT_JS.replace("__MAX_ITEMS__", str(limit)))


def _ref_selector(ref: str) -> str:
    match = REF_RE.match(str(ref or "").strip())
    if not match:
        raise ValueError("Invalid element ref. Expected @e1, @e2, ...")
    return f'[data-actis-chatgpt-ref="e{match.group(1)}"]'


@_tool
def browser_click_ref(ref: str):
    """Click an element returned by browser_snapshot, for example @e3."""
    selector = _ref_selector(ref)
    expression = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return false;
      el.scrollIntoView({{block:'center', inline:'center'}});
      el.click();
      return true;
    }})()"""
    if not js(expression):
        raise RuntimeError("Element ref is stale or no longer exists; take a new snapshot.")
    return {"ok": True, "ref": ref}


@_tool
def browser_type_ref(ref: str, text: str, clear_first: bool = True):
    """Focus a referenced element and type text; optionally clear it first."""
    selector = _ref_selector(ref)
    expression = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return false;
      el.scrollIntoView({{block:'center', inline:'center'}}); el.focus();
      if ({str(bool(clear_first)).lower()}) {{ if ('value' in el) el.value = ''; else if (el.isContentEditable) el.textContent = ''; }}
      return true;
    }})()"""
    if not js(expression):
        raise RuntimeError("Element ref is stale or no longer exists; take a new snapshot.")
    type_text(text)
    return {"ok": True, "ref": ref, "chars": len(text)}
@_tool
def browser_click(x: int, y: int):
    """Click absolute viewport coordinates as a visual fallback."""
    click_at_xy(int(x), int(y))
    return {"ok": True, "x": int(x), "y": int(y)}


@_tool
def browser_type(text: str):
    """Type text into the currently focused element."""
    type_text(text)
    return {"ok": True, "chars": len(text)}


@_tool
def browser_fill(selector: str, text: str, clear_first: bool = True):
    """Fill a CSS selector. Prefer browser_type_ref after browser_snapshot when possible."""
    fill_input(selector, text, clear_first=clear_first)
    return {"ok": True, "selector": selector, "chars": len(text)}


@_tool
def browser_press(key: str, modifiers: int = 0):
    """Press a browser key; modifiers bitfield is 1=Alt 2=Ctrl 4=Meta 8=Shift."""
    press_key(key, modifiers=int(modifiers))
    return {"ok": True, "key": key}


@_tool
def browser_scroll(x: int = 700, y: int = 500, dy: int = -500, dx: int = 0):
    """Scroll the page at viewport coordinates."""
    scroll(int(x), int(y), dy=int(dy), dx=int(dx))
    return {"ok": True}
@_tool
def browser_wait(seconds: float = 1.0):
    """Wait briefly for UI state to settle."""
    value = max(0.0, min(float(seconds), 30.0))
    wait(value)
    return {"ok": True, "seconds": value}


@_tool
def browser_list_tabs():
    """List tabs in the isolated browser profile."""
    return list_tabs()


@_tool
def browser_current_tab():
    """Return the currently active tab."""
    return current_tab()


@_tool
def browser_switch_tab(target: str):
    """Switch tab by target id or URL substring."""
    return {"sessionId": switch_tab(target)}


@_tool
def browser_close_tab(target: str | None = None):
    """Close a tab in the isolated profile."""
    close_tab(target)
    return {"ok": True}


def main() -> None:
    SERVER.run()


if __name__ == "__main__":
    main()
