"""Always-on-top ACTIS GEN desktop assistant for Linux/GTK."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

API = "http://127.0.0.1:8765"
AGENT_ID = os.environ.get("ACTIS_DESKTOP_AGENT_ID", "").strip()
_AGENT_ID_CACHE = AGENT_ID
WINDOW_TITLE = "ACTIS Desktop Assistant"
COLLAPSED_SIZE = 72
PANEL_WIDTH = 340
PANEL_HEIGHT = 320
WINDOW_MARGIN = 12


def api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=360) as response:
        return json.load(response)


def resolved_agent_id() -> str:
    global _AGENT_ID_CACHE
    if _AGENT_ID_CACHE:
        return _AGENT_ID_CACHE
    try:
        agents = api("/api/agents").get("agents", [])
        candidate = next((a for a in agents if "desktop.operator" in list(a.get("skills") or [])), None)
        if candidate:
            _AGENT_ID_CACHE = str(candidate.get("id") or "")
    except (OSError, urllib.error.URLError, ValueError, KeyError):
        pass
    if not _AGENT_ID_CACHE:
        raise RuntimeError("Nenhum agente com skill desktop.operator configurado.")
    return _AGENT_ID_CACHE


def agent_snapshot() -> dict:
    try:
        result = api("/api/agents")
        agent_id = resolved_agent_id()
        return next((a for a in result.get("agents", []) if a.get("id") == agent_id), {})
    except (OSError, urllib.error.URLError, ValueError, KeyError):
        return {}


def _shade(hex_color: str, amount: int) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    parts = [max(0, min(255, int(value[i:i+2], 16) + amount)) for i in (0, 2, 4)]
    return "#" + "".join(f"{part:02x}" for part in parts)


def gen01_desktop_grid() -> list[list[str]]:
    g = [["" for _ in range(14)] for _ in range(12)]
    def put(r: int, c: int, v: str) -> None:
        if 0 <= r < 12 and 0 <= c < 14:
            g[r][c] = v
    def line(r: int, a: int, b: int, v: str) -> None:
        for c in range(a, b + 1):
            put(r, c, v)
    for r, a, b in ((2,4,9),(3,3,10),(4,2,11),(5,2,11),(6,2,11),(7,2,11),(8,3,10),(9,3,10)):
        line(r, a, b, "b")
    for r, c in ((0,4),(0,9),(1,4),(1,5),(1,8),(1,9),(5,1),(5,12)):
        put(r, c, "b")
    for r, c in ((6,1),(6,12),(10,3),(10,4),(10,5),(10,8),(10,9),(10,10),(11,3),(11,4),(11,9),(11,10)):
        put(r, c, "d")
    for r, c in ((3,3),(4,3),(7,3),(8,4)):
        put(r, c, "l")
    for r, c in ((4,10),(7,10),(8,9)):
        put(r, c, "d")
    # Desktop role + DIGITAL expression exactly like ACTIS GEN-01.
    line(4, 3, 10, "v"); line(5, 3, 10, "v"); line(6, 3, 10, "v")
    put(5,5,"a"); put(5,8,"a"); put(6,6,"a"); put(6,7,"a")
    put(7,12,"u"); put(8,12,"u"); put(8,13,"u")
    return g


class PixelCreature(Gtk.DrawingArea):
    def __init__(self, accent: str = "#7aa2f7", pixel: int = 5) -> None:
        super().__init__()
        self.accent = accent
        self.pixel = pixel
        self.grid = gen01_desktop_grid()
        self.set_size_request(14 * pixel, 12 * pixel)
        self.connect("draw", self._draw)

    @staticmethod
    def _rgb(hex_color: str) -> tuple[float, float, float]:
        value = hex_color.lstrip("#")
        return tuple(int(value[i:i+2], 16) / 255 for i in (0, 2, 4))

    def _draw(self, _widget, cr) -> bool:
        species = "#d7ff64"
        palette = {
            "b": species, "l": _shade(species, 28), "d": _shade(species, -58),
            "a": self.accent, "i": "#08090b", "g": "#ffd84a",
            "u": "#56a7ff", "v": "#111722", "w": "#eef5ff",
            "r": "#f7768e", "s": "#a3adba", "n": "#9b6b43",
        }
        px = self.pixel
        for row, cells in enumerate(self.grid):
            for col, cell in enumerate(cells):
                if not cell:
                    continue
                cr.set_source_rgb(*self._rgb(palette[cell]))
                cr.rectangle(col * px, row * px, px, px)
                cr.fill()
                if cell == "v":
                    cr.set_source_rgb(*self._rgb("#64d7ff"))
                    cr.set_line_width(1)
                    cr.rectangle(col * px + .5, row * px + .5, px - 1, px - 1)
                    cr.stroke()
        return False


CSS = b"""
window { background: transparent; }
#shell { background: transparent; }
#panel { background:#090b0e; border:1px solid #202631; border-radius:3px; }
#orb { background:#090c10; border:1px solid #202631; border-radius:3px; }
#orb:hover { border-color:#354052; background:#0c1015; }
#header { background:#0b0e12; border-bottom:1px solid #1d232c; }
#title { color:#edf2f8; font-family:'IBM Plex Mono',monospace; font-size:13px; font-weight:700; }
#meta { color:#687589; font-family:'IBM Plex Mono',monospace; font-size:9px; }
#state { color:#9ece6a; font-family:'IBM Plex Mono',monospace; font-size:9px; }
#messages { background:#080a0d; color:#d5dce7; font-family:'IBM Plex Mono',monospace; font-size:12px; }
#composer { background:#080a0d; border-top:1px solid #20252d; }
entry { background:#0d1015; color:#d8dee9; border:1px solid #29313d; border-radius:3px; padding:8px; font-family:'IBM Plex Mono',monospace; font-size:12px; }
entry:focus { border-color:#46628c; }
button { min-height:30px; background:#11141a; color:#cbd3df; border:1px solid #242831; border-radius:3px; font-family:'IBM Plex Mono',monospace; font-size:10px; }
button:hover { background:#151922; border-color:#3a4452; color:#eef3fb; }
#send { background:#162139; border-color:#345483; color:#cfe0ff; font-weight:600; }
#send:hover { background:#1c2a47; border-color:#4c6f9f; color:#fff; }
"""

class DesktopAssistant(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title(WINDOW_TITLE)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.connect("destroy", Gtk.main_quit)
        self.connect("delete-event", lambda *_: True)
        self.expanded = False
        self.busy = False
        self.conversation_id: str | None = None
        self.agent = agent_snapshot()
        self._build()
        self._apply_css()
        self.connect("realize", self._after_realize)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build(self) -> None:
        self.shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.shell.set_name("shell")
        self.add(self.shell)

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.panel.set_name("panel")
        self.panel.set_size_request(PANEL_WIDTH, PANEL_HEIGHT)
        self.shell.pack_start(self.panel, False, False, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_name("header")
        header.set_border_width(9)
        self.panel.pack_start(header, False, False, 0)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label=self.agent.get("name") or "Desktop Assistant", xalign=0)
        title.set_name("title")
        meta = Gtk.Label(label=f"GEN-01 / DESKTOP  ·  {self.agent.get('model') or 'ACTIS'}", xalign=0)
        meta.set_name("meta")
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(meta, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        self.state = Gtk.Label(label="● READY")
        self.state.set_name("state")
        header.pack_end(self.state, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.messages = Gtk.TextView()
        self.messages.set_name("messages")
        self.messages.set_editable(False)
        self.messages.set_cursor_visible(False)
        self.messages.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.messages.set_left_margin(10)
        self.messages.set_right_margin(10)
        self.messages.set_top_margin(10)
        self.messages.set_bottom_margin(10)
        scroll.add(self.messages)
        self.panel.pack_start(scroll, True, True, 0)

        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        composer.set_name("composer")
        composer.set_border_width(8)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Diga o que quer que eu faça...")
        self.entry.connect("activate", lambda *_: self._send())
        composer.pack_start(self.entry, True, True, 0)
        send = Gtk.Button(label="ENVIAR")
        send.set_name("send")
        send.connect("clicked", lambda *_: self._send())
        composer.pack_start(send, False, False, 0)
        self.panel.pack_end(composer, False, False, 0)

        self.orb = Gtk.EventBox()
        self.orb.set_name("orb")
        self.orb.set_above_child(True)
        self.orb.set_visible_window(True)
        self.orb.set_size_request(COLLAPSED_SIZE, COLLAPSED_SIZE)
        self.orb.add(PixelCreature(self.agent.get("avatar_color") or "#7aa2f7", pixel=5))
        self.orb.connect("button-press-event", self._orb_click)
        self.shell.pack_end(self.orb, False, False, 0)

    def _after_realize(self, *_args) -> None:
        self._set_collapsed_mode()
        self._append("ACTIS", "Assistente pronto. Posso enxergar e controlar este computador.")
        GLib.timeout_add(500, self._enforce_window_state)
        GLib.timeout_add(1600, self._enforce_window_state)

    def _position(self) -> None:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        work = monitor.get_workarea()
        _width, height = self.get_size()
        self.move(work.x + WINDOW_MARGIN, work.y + work.height - height - WINDOW_MARGIN)

    def _set_collapsed_mode(self) -> None:
        self.expanded = False
        self.panel.hide()
        self.resize(COLLAPSED_SIZE, COLLAPSED_SIZE)
        self.set_default_size(COLLAPSED_SIZE, COLLAPSED_SIZE)
        GLib.idle_add(self._reposition_after_resize)
        GLib.idle_add(self._enforce_window_state)

    def _set_expanded_mode(self) -> None:
        self.expanded = True
        self.panel.set_visible(True)
        self.panel.show_all()
        self.resize(PANEL_WIDTH, PANEL_HEIGHT + COLLAPSED_SIZE + 6)
        self.set_default_size(PANEL_WIDTH, PANEL_HEIGHT + COLLAPSED_SIZE + 6)
        self.entry.grab_focus()
        GLib.idle_add(self._reposition_after_resize)
        GLib.idle_add(self._enforce_window_state)

    def _orb_click(self, _widget, event) -> bool:
        if event.button == 1:
            if self.expanded:
                self._set_collapsed_mode()
            else:
                self._set_expanded_mode()
            return True
        if event.button == 3:
            Gtk.main_quit()
            return True
        return False

    def _reposition_after_resize(self) -> bool:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self._position()
        return False

    def _hyprland_env(self) -> dict[str, str] | None:
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        hypr_dir = Path(runtime) / "hypr"
        if not hypr_dir.is_dir():
            return None
        candidates = [p for p in hypr_dir.iterdir() if (p / ".socket.sock").exists()]
        if not candidates:
            return None
        live = max(candidates, key=lambda p: (p / ".socket.sock").stat().st_mtime)
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = runtime
        env["HYPRLAND_INSTANCE_SIGNATURE"] = live.name
        return env

    def _pin_on_hyprland(self) -> None:
        env = self._hyprland_env()
        if env is None:
            return
        try:
            result = subprocess.run(
                ["hyprctl", "-j", "clients"], capture_output=True, text=True, env=env, check=False
            )
            clients = json.loads(result.stdout or "[]")
            client = next((c for c in clients if c.get("title") == WINDOW_TITLE), None)
            if client and not client.get("pinned"):
                subprocess.run(
                    ["hyprctl", "dispatch", "pin", f"address:{client['address']}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False,
                )
        except (OSError, json.JSONDecodeError, StopIteration, KeyError):
            return

    def _enforce_window_state(self) -> bool:
        self.stick()
        self.set_keep_above(True)
        self._position()
        self._pin_on_hyprland()
        for command in (
            ["wmctrl", "-F", "-r", WINDOW_TITLE, "-b", "add,sticky,above,skip_taskbar,skip_pager"],
            ["wmctrl", "-F", "-r", WINDOW_TITLE, "-t", "-1"],
        ):
            try:
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except FileNotFoundError:
                break
        return False

    def _append(self, who: str, text: str) -> None:
        buf = self.messages.get_buffer()
        current = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        prefix = "\n\n" if current else ""
        buf.insert(buf.get_end_iter(), f"{prefix}{who}\n{text}")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.messages.scroll_mark_onscreen(mark)

    def _send(self) -> None:
        prompt = self.entry.get_text().strip()
        if not prompt or self.busy:
            return
        self.entry.set_text("")
        self._append("VOCÊ", prompt)
        self.busy = True
        self.state.set_text("● WORKING")
        threading.Thread(target=self._run_agent, args=(prompt,), daemon=True).start()

    def _ensure_conversation(self) -> str:
        if self.conversation_id:
            return self.conversation_id
        result = api(
            "/api/conversations",
            "POST",
            {"agent_id": AGENT_ID, "title": "Desktop"},
        )
        self.conversation_id = result["conversation"]["id"]
        return self.conversation_id

    def _run_agent(self, prompt: str) -> None:
        try:
            conversation_id = self._ensure_conversation()
            result = api(
                f"/api/conversations/{conversation_id}/messages",
                "POST",
                {"content": prompt, "source": "direct"},
            )
            text = str(result.get("text") or "Concluído.")
            GLib.idle_add(self._finish, text, None)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("error")
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                detail = None
            GLib.idle_add(self._finish, "", detail or f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            GLib.idle_add(self._finish, "", str(exc))

    def _finish(self, text: str, error: str | None) -> bool:
        self.busy = False
        self.state.set_text("● READY" if not error else "● ERROR")
        self._append("ACTIS", text if not error else f"Erro: {error}")
        return False


def main() -> None:
    win = DesktopAssistant()
    win.show_all()
    win._set_collapsed_mode()
    Gtk.main()


if __name__ == "__main__":
    main()
