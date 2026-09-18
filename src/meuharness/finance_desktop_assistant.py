"""Persistent ColorGlass finance assistant for Hyprland/Xwayland."""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

ACTIS_API = "http://127.0.0.1:8765"
ERP_API = "http://127.0.0.1:8770"
ERP_URL = ERP_API + "/?view=reconciliation"
AGENT_ID = "financeiro-colorglass"
WINDOW_TITLE = "ACTIS Financeiro ColorGlass"
COLLAPSED = 74
PANEL_W = 390
PANEL_H = 455
MARGIN = 12
FAST_POLL_SECONDS = 2
SLOW_POLL_SECONDS = 20
ERROR_POLL_SECONDS = 5
NAG_SECONDS = 90

CSS = b"""
window { background: transparent; }
#shell { background: transparent; }
#panel { background:#090b0e; border:1px solid #26303d; border-radius:3px; }
#orb { background:#090c10; border:1px solid #334155; border-radius:3px; }
#orb:hover { border-color:#d7ff64; background:#0d1216; }
#header { background:#0b0e12; border-bottom:1px solid #202833; }
#title { color:#edf2f8; font-family:'IBM Plex Mono',monospace; font-size:13px; font-weight:700; }
#meta { color:#7e8b9d; font-family:'IBM Plex Mono',monospace; font-size:9px; }
#state { color:#d7ff64; font-family:'IBM Plex Mono',monospace; font-size:9px; }
#question { color:#eef3f8; font-family:'IBM Plex Mono',monospace; font-size:12px; }
#detail { color:#a7b2c1; font-family:'IBM Plex Mono',monospace; font-size:10px; }
#reply { color:#d7ff64; font-family:'IBM Plex Mono',monospace; font-size:10px; }
entry { background:#0d1015; color:#d8dee9; border:1px solid #29313d; border-radius:3px; padding:7px; font-family:'IBM Plex Mono',monospace; font-size:11px; }
entry:focus { border-color:#d7ff64; }
button { min-height:30px; background:#11141a; color:#cbd3df; border:1px solid #242831; border-radius:3px; font-family:'IBM Plex Mono',monospace; font-size:10px; }
button:hover { background:#151922; border-color:#3a4452; color:#eef3fb; }
#account-button { min-height:32px; }
#account-button:disabled { opacity:.55; }
#account-picker { background:#0d1015; border:1px solid #29313d; border-radius:3px; padding:7px; }
#account-search { min-height:30px; }
#account-list { background:transparent; }
.account-row-button { min-height:32px; padding:5px 7px; background:transparent; border:0; color:#d8dee9; }
.account-row-button:hover { background:#17202a; border-radius:2px; }
.account-code { color:#9ece6a; }
.account-empty { color:#8f9aa7; font-family:'IBM Plex Mono',monospace; font-size:10px; padding:8px 2px; }
"""


def http_json(base: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


class ActisBot(Gtk.DrawingArea):
    '''Exact ACTIS GEN BOT-02 renderer for Financeiro ColorGlass.'''

    ACCENT = "#d7ff64"
    COMPANY_COLOR = "#2f93ff"
    BLINK_DELAY = 1.351
    BREATHE_DELAY = 1.351

    def __init__(self, accent: str = "#d7ff64") -> None:
        super().__init__()
        self.accent = accent
        self.set_size_request(COLLAPSED, COLLAPSED)
        self._started = time.monotonic()
        self._reduced_motion = os.environ.get("GTK_DISABLE_ANIMATION") == "1"
        self._animation_source: int | None = None
        self.connect("draw", self._draw)
        self.connect("destroy", self._stop_animation)
        if not self._reduced_motion:
            self._animation_source = GLib.timeout_add(90, self._animate)

    def _animate(self) -> bool:
        self.queue_draw()
        return True

    def _stop_animation(self, *_args) -> None:
        if self._animation_source:
            GLib.source_remove(self._animation_source)
            self._animation_source = None

    def _gaze(self, width: float, height: float) -> tuple[float, float]:
        if self._reduced_motion or not self.get_window():
            return 0.0, 0.0
        try:
            pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
            pos = pointer.get_position()
            px, py = float(pos[-2]), float(pos[-1])
            origin = self.get_window().get_origin()
            ox, oy = float(origin[-2]), float(origin[-1])
            dx = px - (ox + width * 0.5)
            dy = py - (oy + height * 0.43)
            distance = math.hypot(dx, dy)
            amount = min(1.0, distance / max(100.0, width * 1.5))
            if not distance:
                return 0.0, 0.0
            return (dx / distance) * 6.0 * amount, (dy / distance) * 5.0 * amount
        except Exception:
            return 0.0, 0.0

    def _blink_scale(self, elapsed: float) -> float:
        if self._reduced_motion:
            return 1.0
        pct = (((elapsed + self.BLINK_DELAY) % 6.4) / 6.4) * 100.0
        for center in (43.0, 93.0):
            d = abs(pct - center)
            if d <= 2.0:
                return 0.08 + 0.92 * (d / 2.0)
        return 1.0

    def _breathe(self, elapsed: float) -> tuple[float, float, float]:
        if self._reduced_motion:
            return 0.0, 1.0, 1.0
        phase = ((elapsed + self.BREATHE_DELAY) % 4.4) / 4.4
        amount = (1.0 - math.cos(phase * math.tau)) * 0.5
        return -1.7 * amount, 1.0 + 0.012 * amount, 1.0 + 0.018 * amount

    def _svg(self, elapsed: float, width: float, height: float) -> bytes:
        gaze_x, gaze_y = self._gaze(width, height)
        blink = self._blink_scale(elapsed)
        breathe_y, scale_x, scale_y = self._breathe(elapsed)
        float_transform = (
            f"translate(70 106) translate(0 {breathe_y:.3f}) "
            f"scale({scale_x:.5f} {scale_y:.5f}) translate(-70 -106)"
        )
        blink_transform = f"translate(71 47) scale(1 {blink:.5f}) translate(-71 -47)"
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="140" height="120" viewBox="0 0 140 120">
<ellipse cx="70" cy="111" rx="27" ry="4" fill="#000" opacity=".18"/>
<g transform="{float_transform}">
  <g>
    <ellipse cx="70" cy="62" rx="39" ry="44" transform="rotate(-12 70 62)" fill="#fff"/>
    <svg x="0" y="72" width="140" height="40" viewBox="0 72 140 40" overflow="hidden">
      <ellipse cx="70" cy="62" rx="39" ry="44" transform="rotate(-12 70 62)" fill="{self.COMPANY_COLOR}"/>
      <path d="M45 94 Q70 107 94 91" fill="none" stroke="#000" stroke-opacity=".12" stroke-width="2"/>
    </svg>
    <g transform="translate({gaze_x:.3f} {gaze_y:.3f})">
      <g transform="{blink_transform}" fill="#08090b">
        <ellipse cx="60" cy="49" rx="5" ry="8" transform="rotate(-12 60 49)"/>
        <ellipse cx="82" cy="42" rx="7" ry="14" transform="rotate(-12 82 42)"/>
      </g>
    </g>
  </g>
  <g transform="rotate(10 111 85)">
    <path d="M97 65h15l3 3 3-3h14v34h-14l-3 3-3-3H97z" fill="{self.accent}" stroke="#444d5c" stroke-width="2"/>
    <path d="M115 68v30m-13-24h8m-8 6h8m-8 6h6m13-12h7m-7 6h7m-7 6h5" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
  </g>
</g>
</svg>'''.encode("utf-8")

    def _draw(self, _widget, cr) -> bool:
        width = max(1, self.get_allocated_width())
        height = max(1, self.get_allocated_height())
        elapsed = time.monotonic() - self._started
        render_w = width
        render_h = int(round(render_w * 120 / 140))
        if render_h > height:
            render_h = height
            render_w = int(round(render_h * 140 / 120))
        x = (width - render_w) / 2.0
        y = (height - render_h) / 2.0
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(render_w, render_h)
            loader.write(self._svg(elapsed, width, height))
            loader.close()
            pixbuf = loader.get_pixbuf()
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)
            cr.paint()
        except Exception:
            return False
        return False


class FinanceAssistant(Gtk.Window):
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
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.connect("delete-event", lambda *_: True)
        self.expanded = False
        self.polling = False
        self.busy = False
        self.hidden_complete = False
        self.pending: list[dict] = []
        self.accounts: list[dict] = []
        self.account_rows: dict[str, Gtk.ListBoxRow] = {}
        self.current: dict | None = None
        self.conversation_id: str | None = None
        self.preserve_agent_reply = False
        self.selected_account_code = ""
        self.selected_account_name = ""
        self.state_hash = ""
        self.poll_source_id: int | None = None
        self.poll_generation = 0
        self.poll_again = False
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
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.panel.set_name("panel")
        self.panel.set_size_request(PANEL_W, PANEL_H)
        self.panel.set_border_width(10)
        self.shell.pack_start(self.panel, False, False, 0)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_name("header")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Financeiro ColorGlass", xalign=0)
        title.set_name("title")
        meta = Gtk.Label(label="ACTIS · CONCILIAÇÃO BANCÁRIA", xalign=0)
        meta.set_name("meta")
        titles.pack_start(title, False, False, 0)
        titles.pack_start(meta, False, False, 0)
        header.pack_start(titles, True, True, 0)
        self.state = Gtk.Label(label="● VERIFICANDO")
        self.state.set_name("state")
        header.pack_end(self.state, False, False, 0)
        self.panel.pack_start(header, False, False, 0)

        self.question = Gtk.Label(xalign=0, yalign=0)
        self.question.set_name("question")
        self.question.set_line_wrap(True)
        self.question.set_line_wrap_mode(2)
        self.panel.pack_start(self.question, False, False, 0)
        self.detail = Gtk.Label(xalign=0, yalign=0)
        self.detail.set_name("detail")
        self.detail.set_line_wrap(True)
        self.detail.set_line_wrap_mode(2)
        self.panel.pack_start(self.detail, False, False, 0)

        self.account_button = Gtk.Button(label="Escolher conta...")
        self.account_button.set_name("account-button")
        self.account_button.set_halign(Gtk.Align.FILL)
        self.account_button.connect("clicked", self._toggle_account_picker)
        self.panel.pack_start(self.account_button, False, False, 0)

        self.account_revealer = Gtk.Revealer()
        self.account_revealer.set_reveal_child(False)
        self.account_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.account_revealer.set_transition_duration(160)
        picker = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        picker.set_name("account-picker")
        picker.set_border_width(6)
        self.account_search = Gtk.Entry()
        self.account_search.set_name("account-search")
        self.account_search.set_placeholder_text("Buscar conta ou código...")
        self.account_search.connect("changed", self._filter_accounts)
        picker.pack_start(self.account_search, False, False, 0)
        account_scroll = Gtk.ScrolledWindow()
        account_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        account_scroll.set_min_content_height(96)
        account_scroll.set_max_content_height(132)
        self.account_list = Gtk.ListBox()
        self.account_list.set_name("account-list")
        self.account_list.set_selection_mode(Gtk.SelectionMode.NONE)
        account_scroll.add(self.account_list)
        picker.pack_start(account_scroll, True, True, 0)
        self.account_empty = Gtk.Label(label="Nenhuma conta encontrada.")
        self.account_empty.set_name("account-empty")
        self.account_empty.set_no_show_all(True)
        picker.pack_start(self.account_empty, False, False, 0)
        self.account_revealer.add(picker)
        self.panel.pack_start(self.account_revealer, False, False, 0)

        self.reply = Gtk.Entry()
        self.reply.set_placeholder_text("Responda: ex. frete, alumínio, retirada do sócio...")
        self.reply.connect("activate", lambda *_: self._send_answer())
        self.panel.pack_start(self.reply, False, False, 0)
        self.agent_reply = Gtk.Label(xalign=0, yalign=0)
        self.agent_reply.set_name("reply")
        self.agent_reply.set_line_wrap(True)
        self.agent_reply.set_line_wrap_mode(2)
        self.panel.pack_start(self.agent_reply, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mark = Gtk.Button(label="MARCAR")
        mark.connect("clicked", lambda *_: self._mark_direct())
        answer = Gtk.Button(label="RESPONDER")
        answer.connect("clicked", lambda *_: self._send_answer())
        open_erp = Gtk.Button(label="ABRIR ERP")
        open_erp.connect("clicked", lambda *_: self._open_erp())
        actions.pack_start(mark, True, True, 0)
        actions.pack_start(answer, True, True, 0)
        actions.pack_start(open_erp, True, True, 0)
        self.panel.pack_end(actions, False, False, 0)

        self.orb = Gtk.EventBox()
        self.orb.set_name("orb")
        self.orb.set_visible_window(True)
        self.orb.set_above_child(True)
        self.orb.set_size_request(COLLAPSED, COLLAPSED)
        self.orb.add(ActisBot("#d7ff64"))
        self.orb.connect("button-press-event", self._orb_click)
        self.shell.pack_end(self.orb, False, False, 0)

    def _after_realize(self, *_args) -> None:
        self._set_collapsed()
        GLib.timeout_add(1, self._poll_once)
        GLib.timeout_add_seconds(NAG_SECONDS, self._nag_tick)
        GLib.timeout_add(600, self._enforce_window_state)

    def _monitor_workarea(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_workarea()

    def _position(self, extra_x: int = 0) -> None:
        work = self._monitor_workarea()
        width, height = self.get_size()
        x = work.x + work.width - width - MARGIN + extra_x
        y = work.y + work.height - height - MARGIN
        self.move(x, y)

    def _set_collapsed(self) -> None:
        self.expanded = False
        self.panel.hide()
        self.resize(COLLAPSED, COLLAPSED)
        GLib.idle_add(self._reposition)

    def _set_expanded(self, open_erp: bool = False) -> None:
        self.expanded = True
        self.panel.show_all()
        self.account_revealer.set_reveal_child(False)
        self.resize(PANEL_W, PANEL_H + COLLAPSED + 6)
        self.reply.grab_focus()
        GLib.idle_add(self._reposition)
        if open_erp:
            self._open_erp()

    def _reposition(self) -> bool:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self._position()
        return False

    def _orb_click(self, _widget, event) -> bool:
        if event.button == 1:
            self._set_expanded(open_erp=True)
            return True
        if event.button == 3:
            self._set_collapsed()
            return True
        return False

    def _open_erp(self) -> None:
        try:
            subprocess.Popen(
                ["xdg-open", ERP_URL],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            self.agent_reply.set_text(f"Não consegui abrir o ERP: {exc}")

    def _schedule_poll(self, delay: int) -> None:
        if self.poll_source_id:
            GLib.source_remove(self.poll_source_id)
            self.poll_source_id = None
        self.poll_source_id = GLib.timeout_add_seconds(delay, self._poll_tick)

    def _schedule_next_poll(self) -> None:
        delay = FAST_POLL_SECONDS if self.pending else SLOW_POLL_SECONDS
        self._schedule_poll(delay)

    def _poll_tick(self) -> bool:
        self.poll_source_id = None
        if self.polling:
            return False
        self.polling = True
        self.poll_generation += 1
        generation = self.poll_generation
        threading.Thread(target=self._poll_worker, args=(generation,), daemon=True).start()
        return False

    def _poll_once(self) -> bool:
        self._poll_tick()
        return False

    def _poll_worker(self, generation: int) -> None:
        try:
            payload = http_json(ERP_API, "/api/assistant/pending")
            GLib.idle_add(self._apply_state, payload, None, generation)
        except Exception as exc:
            GLib.idle_add(self._apply_state, {}, str(exc), generation)

    def _apply_state(self, payload: dict, error: str | None, generation: int) -> bool:
        if generation != self.poll_generation:
            return False
        self.polling = False
        if error:
            self.state.set_text("● ERP OFFLINE")
            self.agent_reply.set_text("Estou tentando reconectar ao financeiro.")
            if not self.get_visible():
                self.show_all()
                self._set_collapsed()
            self._schedule_poll(ERROR_POLL_SECONDS)
            return False
        self.pending = list(payload.get("pending") or [])
        self.accounts = list(payload.get("chart_of_accounts") or [])
        incoming_hash = str(payload.get("state_hash") or "")
        state_changed = bool(incoming_hash and incoming_hash != self.state_hash)
        self.state_hash = incoming_hash
        if not self.pending:
            self.current = None
            if self.hidden_complete:
                self._schedule_next_poll()
                return False
            self.state.set_text("● CONCLUÍDO")
            self.question.set_text("Conciliação fechada. Bom trabalho.")
            self.detail.set_text("Não ficou nenhuma transação pendente neste mês.")
            self.agent_reply.set_text("Até aparecer algo novo.")
            if not self.get_visible():
                self.show_all()
            self._set_expanded()
            GLib.timeout_add(2200, self._slide_away)
            self._schedule_next_poll()
            return False

        previous_key = str((self.current or {}).get("bank_transaction_key") or "")
        next_key = str((self.pending[0] or {}).get("bank_transaction_key") or "")
        transaction_changed = previous_key != next_key
        self.hidden_complete = False
        self.current = self.pending[0]
        if not self.get_visible():
            self.show_all()
            self._set_collapsed()
        self.state.set_text(f"● {len(self.pending)} PENDENTE(S)")
        keep_reply = self.preserve_agent_reply and not transaction_changed
        self._refresh_transaction_ui(keep_reply=keep_reply, reset_account=transaction_changed)
        self.preserve_agent_reply = False
        if state_changed:
            self._schedule_poll(FAST_POLL_SECONDS)
        else:
            self._schedule_next_poll()
        return False

    def _account_label(self) -> str:
        if self.selected_account_code and self.selected_account_name:
            return f"{self.selected_account_code} · {self.selected_account_name}"
        return "Escolher conta..."

    def _rebuild_account_rows(self) -> None:
        for row in list(self.account_list.get_children()):
            row.destroy()
        self.account_rows = {}
        valid_codes = {str(item.get("code") or "") for item in self.accounts if item.get("code")}
        if self.selected_account_code not in valid_codes:
            self.selected_account_code = ""
            self.selected_account_name = ""
        self.account_button.set_label(self._account_label())
        self.account_button.set_sensitive(bool(self.accounts))
        for item in self.accounts:
            code = str(item.get("code") or "")
            name = str(item.get("name") or "")
            if not code:
                continue
            row = Gtk.ListBoxRow()
            row.set_name("account-row")
            button = Gtk.Button()
            button.get_style_context().add_class("account-row-button")
            label = Gtk.Label(label=f"{code} · {name}", xalign=0)
            button.add(label)
            row.add(button)
            button.connect("clicked", lambda *_args, value=item: self._choose_account(value))
            self.account_list.add(row)
            self.account_rows[code] = row
        self.account_list.show_all()
        self._filter_accounts()

    @staticmethod
    def _escape_markup(value: str) -> str:
        return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))

    def _toggle_account_picker(self, *_args) -> None:
        reveal = not self.account_revealer.get_reveal_child()
        if reveal:
            self._rebuild_account_rows()
            self.account_search.set_text("")
            self._filter_accounts()
        self.account_revealer.set_reveal_child(reveal)
        if reveal:
            self.account_search.grab_focus()

    def _filter_accounts(self, *_args) -> None:
        query = self.account_search.get_text().strip().casefold()
        visible = 0
        for code, row in self.account_rows.items():
            item = next((value for value in self.accounts if str(value.get("code") or "") == code), {})
            text = f"{code} {item.get('name') or ''}".casefold()
            matches = not query or query in text
            row.set_visible(matches)
            if matches:
                visible += 1
        self.account_empty.set_visible(visible == 0)

    def _choose_account(self, item: dict) -> None:
        code = str(item.get("code") or "")
        if not code:
            return
        self.selected_account_code = code
        self.selected_account_name = str(item.get("name") or "")
        self.account_button.set_label(self._account_label())
        self.account_revealer.set_reveal_child(False)
        self.reply.grab_focus()

    def _refresh_transaction_ui(self, keep_reply: bool = False, reset_account: bool = False) -> None:
        tx = self.current or {}
        direction = "ENTRADA" if tx.get("direction") == "credit" else "SAÍDA"
        amount = abs(float(tx.get("amount") or 0))
        self.question.set_text(
            f"Preciso fechar esta {direction}: R$ {amount:,.2f}. O que foi isso?"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )
        self.detail.set_text(f"{tx.get('date','')} · {tx.get('description','')}")
        if reset_account:
            self.selected_account_code = ""
            self.selected_account_name = ""
        self._rebuild_account_rows()
        self.account_revealer.set_reveal_child(False)
        if not keep_reply:
            self.reply.set_text("")
            self.agent_reply.set_text("Você pode responder em texto ou escolher a conta e clicar MARCAR.")

    def _mark_direct(self) -> None:
        if self.busy or not self.current:
            return
        account_code = self.selected_account_code
        if not account_code:
            self.agent_reply.set_text("Escolha a conta primeiro.")
            return
        payload = {
            "bank_transaction_key": self.current.get("bank_transaction_key"),
            "account_code": account_code,
            "notes": self.reply.get_text().strip(),
        }
        self.busy = True
        self.state.set_text("● SALVANDO")
        threading.Thread(target=self._mark_worker, args=(payload,), daemon=True).start()

    def _mark_worker(self, payload: dict) -> None:
        try:
            http_json(ERP_API, "/api/reconciliation/confirm", "POST", payload)
            GLib.idle_add(self._finish_action, "Marcado. Próxima.")
        except Exception as exc:
            GLib.idle_add(self._finish_action, f"Erro ao marcar: {exc}")

    def _send_answer(self) -> None:
        if self.busy or not self.current:
            return
        answer = self.reply.get_text().strip()
        if not answer:
            self.agent_reply.set_text("Me diga o que foi essa transação.")
            return
        self.busy = True
        self.state.set_text("● PENSANDO")
        threading.Thread(target=self._agent_worker, args=(answer,), daemon=True).start()

    def _ensure_conversation(self) -> str:
        if self.conversation_id:
            return self.conversation_id
        result = http_json(
            ACTIS_API,
            "/api/conversations",
            "POST",
            {"agent_id": AGENT_ID, "title": "Conciliação bancária desktop"},
        )
        self.conversation_id = result["conversation"]["id"]
        return self.conversation_id

    def _agent_worker(self, answer: str) -> None:
        try:
            tx = self.current or {}
            accounts = "; ".join(
                f"{a.get('code')}={a.get('name')}" for a in self.accounts if a.get("code")
            )
            prompt = (
                "Estamos conciliando UMA transação bancária. "
                f"Chave: {tx.get('bank_transaction_key')}; data: {tx.get('date')}; "
                f"direção: {tx.get('direction')}; valor: {tx.get('amount')}; "
                f"descrição bancária: {tx.get('description')}. "
                f"Plano de contas: {accounts}. "
                f"O humano respondeu: {answer!r}. "
                "Se a resposta deixar a classificação inequívoca, use finance_reconcile_transaction "
                "para esta chave, com o account_code correto, notes igual à resposta do humano e "
                "human_confirmed=true. Se houver ambiguidade real, NÃO confirme: faça apenas uma "
                "pergunta curta e objetiva para destravar."
            )
            conversation = self._ensure_conversation()
            result = http_json(
                ACTIS_API,
                f"/api/conversations/{conversation}/messages",
                "POST",
                {"content": prompt, "source": "direct"},
            )
            text = str(result.get("text") or "Concluído.")
            GLib.idle_add(self._finish_action, text)
        except Exception as exc:
            GLib.idle_add(self._finish_action, f"Erro no Financeiro: {exc}")

    def _finish_action(self, text: str) -> bool:
        self.busy = False
        self.agent_reply.set_text(text)
        self.preserve_agent_reply = True
        self.state.set_text("● ATUALIZANDO")
        GLib.idle_add(self._poll_once)
        return False

    def _nag_tick(self) -> bool:
        if self.pending and not self.busy:
            if not self.get_visible():
                self.show_all()
                self._set_collapsed()
            if not self.expanded:
                self._set_expanded()
                self.agent_reply.set_text("Ainda estou esperando você fechar essa conciliação.")
        return True

    def _slide_away(self) -> bool:
        if self.pending:
            return False
        work = self._monitor_workarea()
        width, _height = self.get_size()
        start_x = work.x + work.width - width - MARGIN
        def step(i: int = 0) -> bool:
            if self.pending:
                return False
            if i >= 16:
                self.hide()
                self.hidden_complete = True
                return False
            self.move(start_x + int((width + MARGIN + 20) * (i + 1) / 16), self.get_position()[1])
            GLib.timeout_add(18, step, i + 1)
            return False
        step()
        return False

    def _hyprland_env(self) -> dict[str, str] | None:
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        root = Path(runtime) / "hypr"
        if not root.is_dir():
            return None
        candidates = [p for p in root.iterdir() if (p / ".socket.sock").exists()]
        if not candidates:
            return None
        live = max(candidates, key=lambda p: (p / ".socket.sock").stat().st_mtime)
        env = os.environ.copy()
        env["HYPRLAND_INSTANCE_SIGNATURE"] = live.name
        env["XDG_RUNTIME_DIR"] = runtime
        return env

    def _enforce_window_state(self) -> bool:
        self.stick()
        self.set_keep_above(True)
        if self.get_visible():
            self._position()
        env = self._hyprland_env()
        if env:
            try:
                result = subprocess.run(
                    ["hyprctl", "-j", "clients"], capture_output=True, text=True, env=env, check=False
                )
                clients = json.loads(result.stdout or "[]")
                client = next((c for c in clients if c.get("title") == WINDOW_TITLE), None)
                if client and not client.get("pinned"):
                    subprocess.run(
                        ["hyprctl", "dispatch", "pin", f"address:{client['address']}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        check=False,
                    )
            except (OSError, ValueError, json.JSONDecodeError, KeyError):
                pass
        return True


def main() -> None:
    window = FinanceAssistant()
    window.show_all()
    window._set_collapsed()
    Gtk.main()


if __name__ == "__main__":
    main()
