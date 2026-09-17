"""ACTIS GEN local browser UI and API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

_GLOBAL_CHAT_BUSY_LOCK = threading.Lock()
_GLOBAL_CHAT_BUSY_AGENTS: set[str] = set()

from meuharness import HarnessError
from meuharness.artifacts import artifact_file, list_run_attachments
from meuharness.chat_attachments import history_content_with_attachment_refs, normalize_chat_attachments
from meuharness.agents import (
    append_conversation_message,
    cancel_run_record,
    interrupt_stale_running_runs,
    create_agent,
    create_company,
    create_conversation,
    create_sector,
    get_agent,
    get_conversation,
    list_agents,
    list_companies,
    list_conversations,
    list_runs,
    list_sectors,
    update_agent,
    update_company,
    update_sector,
)
from meuharness.approvals import (
    list_approvals,
    request_approval,
    resolve_approval,
    revoke_approval,
)
from meuharness.automation_scheduler import start_scheduler
from meuharness.automations import (
    create_automation,
    delete_automation,
    get_automation,
    list_automations,
    update_automation,
)
from meuharness.browser_control import action as browser_action
from meuharness.browser_control import snapshot as browser_snapshot
from meuharness.channels import (
    create_channel,
    create_channel_message,
    delete_channel,
    delete_channel_message,
    get_channel,
    list_channel_messages,
    list_channels,
    set_channel_members,
    update_channel_message,
)
from meuharness.connectors import delete_connector, list_connectors, save_connector, test_connector
from meuharness.company_bus import list_company_tasks
from meuharness.contexts import (
    create_context_item,
    create_project,
    delete_context_item,
    list_agent_bindings,
    list_context_items,
    list_projects,
    set_agent_project_bindings,
)
from meuharness.durable_runs import list_checkpoints
from meuharness.event_bus import list_events
from meuharness.execution_service import cancel_active_run, execute_agent, resume_run
from meuharness.memory import recall
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.skills import (
    list_skill_catalog,
    runtime_features_for_skills,
    scopes_for_skills,
    tools_for_skills,
)
from meuharness.storage import DB_FILE
from meuharness.tasks import list_tasks, reconcile_tasks_with_runs
from meuharness.tool_registry import (
    list_tool_catalog,
    normalize_scopes,
    normalize_tool_ids,
    scopes_for_tools,
)
from meuharness.workflows import (
    delete_workflow,
    get_workflow,
    list_workflow_runs,
    list_workflows,
    run_workflow,
    save_workflow,
    validate_workflow,
)

INDEX = Path(__file__).with_name("web_assets") / "index.html"


_SYSTEM_USAGE_LOCK = threading.Lock()
_SYSTEM_USAGE_CPU: dict[int, float] = {}
_SYSTEM_USAGE_AT: float | None = None


def _actis_process_snapshot() -> tuple[dict[int, float], int, int]:
    """Return CPU seconds, RSS bytes and process count for ACTIS + child processes."""
    rows: dict[int, tuple[int, float, int, str]] = {}
    ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            stat_tail = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            ppid = int(stat_tail[1])
            cpu_seconds = (int(stat_tail[11]) + int(stat_tail[12])) / ticks_per_second
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="ignore").strip()
            rss_kb = 0
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
            rows[pid] = (ppid, cpu_seconds, rss_kb * 1024, cmdline)
        except (OSError, ValueError, IndexError):
            continue

    roots = {pid for pid, row in rows.items() if "-m meuharness." in row[3]}
    children: dict[int, list[int]] = {}
    for pid, (ppid, *_rest) in rows.items():
        children.setdefault(ppid, []).append(pid)
    active = set(roots)
    stack = list(roots)
    while stack:
        parent = stack.pop()
        for child in children.get(parent, []):
            if child not in active:
                active.add(child)
                stack.append(child)

    cpu = {pid: rows[pid][1] for pid in active if pid in rows}
    ram_bytes = sum(rows[pid][2] for pid in active if pid in rows)
    return cpu, ram_bytes, len(active)


def _actis_system_usage() -> dict[str, object]:
    global _SYSTEM_USAGE_CPU, _SYSTEM_USAGE_AT
    now = time.monotonic()
    current, ram_bytes, process_count = _actis_process_snapshot()
    with _SYSTEM_USAGE_LOCK:
        if _SYSTEM_USAGE_AT is None:
            cpu_percent = 0.0
        else:
            elapsed = max(0.001, now - _SYSTEM_USAGE_AT)
            used = sum(max(0.0, value - _SYSTEM_USAGE_CPU.get(pid, value)) for pid, value in current.items())
            cpu_percent = used / elapsed * 100.0 / max(1, os.cpu_count() or 1)
        _SYSTEM_USAGE_CPU = current
        _SYSTEM_USAGE_AT = now
    return {
        "cpu_percent": round(cpu_percent, 1),
        "ram_bytes": ram_bytes,
        "ram_mb": round(ram_bytes / (1024 * 1024), 1),
        "processes": process_count,
    }


def _agent_tools_and_scopes(agent: dict) -> tuple[set[str], set[str]]:
    skill_ids = list(agent.get("skills") or [])
    tools = set(normalize_tool_ids([*list(agent.get("tools") or []), *tools_for_skills(skill_ids)]))
    if "permissions" in agent:
        scopes = set(normalize_scopes(agent.get("permissions") or []))
    else:
        scopes = set(scopes_for_tools(list(tools))) | set(scopes_for_skills(skill_ids))
    return tools, scopes


def _agent_has_whatsapp(agent: dict) -> bool:
    return "whatsapp.local" in runtime_features_for_skills(list(agent.get("skills") or []))


def _agent_has_logistics_erp(agent: dict) -> bool:
    return "logistics.erp.local" in runtime_features_for_skills(list(agent.get("skills") or []))


class Handler(BaseHTTPRequestHandler):
    env_file: str | None = None

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _cors_origin(self) -> str | None:
        origin = str(self.headers.get("Origin") or "").strip()
        allowed = {"http://localhost", "https://localhost", "capacitor://localhost"}
        allowed.update(
            item.strip()
            for item in os.environ.get("ACTIS_CORS_ORIGINS", "").split(",")
            if item.strip()
        )
        return origin if origin in allowed else None

    def end_headers(self) -> None:
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(value, dict):
            raise TypeError("JSON inválido")
        return value

    def router_dashboard_json(self, path: str) -> dict | None:
        cookie = self.headers.get("Cookie")
        if not cookie:
            return None
        req = urllib.request.Request(
            "http://127.0.0.1:20128" + path,
            headers={"Cookie": cookie, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                value = json.load(response)
                return value if isinstance(value, dict) else None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

    def router_dashboard_post(self, path: str, payload: dict) -> dict | None:
        cookie = self.headers.get("Cookie")
        if not cookie:
            return None
        raw = json.dumps(payload).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:20128" + path,
            data=raw,
            method="POST",
            headers={
                "Cookie": cookie,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                value = json.load(response)
                return value if isinstance(value, dict) else None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/":
            raw = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if u.path == "/assets/visual-system.css":
            asset = INDEX.parent / "visual-system.css"
            raw = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if u.path in {"/assets/whatsapp-view.css", "/assets/whatsapp-view.js", "/assets/whatsapp-crm-mini.css", "/assets/whatsapp-crm-mini.js", "/assets/logistics-erp.css", "/assets/logistics-erp.js"}:
            asset_name = u.path.rsplit("/", 1)[-1]
            asset = INDEX.parent / asset_name
            raw = asset.read_bytes()
            content_type = "text/css; charset=utf-8" if asset_name.endswith(".css") else "text/javascript; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if u.path.startswith("/api/artifacts/") and u.path.endswith("/download"):
            artifact_id = u.path.split("/")[3]
            try:
                artifact, file_path = artifact_file(artifact_id)
            except (FileNotFoundError, PermissionError):
                self.send_json(404, {"error": "Arquivo não encontrado."})
                return
            size = file_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", str(artifact.get("mime_type") or "application/octet-stream"))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(str(artifact.get("name") or file_path.name)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with file_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
            return
        if u.path == "/api/models":
            try:
                s = NineRouterSettings.from_env(self.env_file)
                models = asyncio.run(NineRouterGateway(s).list_models())
                self.send_json(200, {"models": models, "selected": s.model})
                return
            except Exception as exc:  # noqa: BLE001
                self.send_json(500, {"error": str(exc)})
                return
        if u.path == "/api/model-health":
            try:
                settings = NineRouterSettings.from_env(self.env_file)
                models = list(asyncio.run(NineRouterGateway(settings).list_models()))
                latest: dict[str, dict] = {}
                for run in list_runs():
                    model = str(run.get("model") or "")
                    if model and model not in latest:
                        latest[model] = run
                health = {m: {"model": m, "status": "unknown", "source": "catalog"} for m in models}
                for model, run in latest.items():
                    if model not in health:
                        continue
                    run_status = str(run.get("status") or "")
                    if run_status in {"completed", "planned", "blocked"}:
                        # These states require a valid model response. Planned/blocked describe
                        # execution semantics, not provider health.
                        health[model]["status"] = "working"
                        health[model]["source"] = "last_run"
                    elif run_status == "failed":
                        health[model]["status"] = "failing"
                        health[model]["source"] = "last_run"

                provider_payload = self.router_dashboard_json("/api/providers") or {}
                prefix_by_provider = {
                    "codex": "cx",
                    "gemini-cli": "gc",
                    "github": "gh",
                    "kimi": "kimi",
                }
                for connection in provider_payload.get("connections", []):
                    if not isinstance(connection, dict):
                        continue
                    prefix = prefix_by_provider.get(str(connection.get("provider") or ""))
                    if not prefix or not connection.get("isActive", True):
                        continue
                    test_status = str(connection.get("testStatus") or "unknown")
                    if test_status not in {"active", "unavailable"}:
                        continue
                    status = "working" if test_status == "active" else "failing"
                    for model in models:
                        if model.startswith(prefix + "/") and health[model]["source"] != "last_run":
                            health[model].update({"status": status, "source": "9router_provider"})

                availability = self.router_dashboard_json("/api/models/availability") or {}
                for issue in availability.get("models", []):
                    if not isinstance(issue, dict):
                        continue
                    provider = str(issue.get("provider") or "")
                    model_name = str(issue.get("model") or "")
                    prefix = prefix_by_provider.get(provider)
                    for model in models:
                        affected = (
                            model_name != "__all"
                            and (model == model_name or model.endswith("/" + model_name))
                        ) or (model_name == "__all" and prefix and model.startswith(prefix + "/"))
                        if affected:
                            health[model].update(
                                {"status": "failing", "source": "9router_availability"}
                            )

                self.send_json(200, {"models": list(health.values()), "token_cost": 0})
                return
            except Exception as exc:  # noqa: BLE001
                self.send_json(500, {"error": str(exc)})
                return
        if u.path == "/api/provider-connections":
            payload = self.router_dashboard_json("/api/providers")
            if payload is None:
                self.send_json(401, {"error": "Sessão do 9Router não disponível neste navegador."})
                return
            connections = []
            for item in payload.get("connections", []):
                if not isinstance(item, dict):
                    continue
                connections.append(
                    {
                        "id": item.get("id"),
                        "provider": item.get("provider"),
                        "name": item.get("name") or item.get("email") or item.get("provider"),
                        "auth_type": item.get("authType"),
                        "active": item.get("isActive", True),
                        "test_status": item.get("testStatus") or "unknown",
                        "last_error": item.get("lastError"),
                    }
                )
            self.send_json(200, {"connections": connections})
            return
        if u.path == "/api/connectors":
            self.send_json(200, {"connectors": list_connectors()})
            return
        if u.path == "/api/tools":
            self.send_json(200, {"tools": list_tool_catalog()})
            return
        if u.path == "/api/skills":
            self.send_json(200, {"skills": list_skill_catalog()})
            return
        if u.path == "/api/agents":
            self.send_json(200, {"agents": list_agents()})
            return
        if u.path == "/api/channels":
            self.send_json(200, {"channels": list_channels()})
            return
        if u.path.startswith("/api/channels/") and u.path.endswith("/messages"):
            channel_id = u.path.split("/")[3]
            channel = get_channel(channel_id)
            if not channel:
                self.send_json(404, {"error": "Canal não encontrado."})
                return
            self.send_json(200, {"channel": channel, "messages": list_channel_messages(channel_id)})
            return
        if u.path.startswith("/api/channels/"):
            channel_id = u.path.split("/")[3]
            channel = get_channel(channel_id)
            if not channel:
                self.send_json(404, {"error": "Canal não encontrado."})
                return
            self.send_json(200, {"channel": channel})
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/messages"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent:
                self.send_json(404, {"error": "Agente não encontrado."})
                return
            if not _agent_has_whatsapp(agent):
                self.send_json(404, {"error": "Skill WhatsApp não está habilitada neste agente."})
                return
            query = parse_qs(u.query)
            contact = str((query.get("contact") or [""])[0]).strip() or None
            try:
                limit = max(1, min(int((query.get("limit") or ["200"])[0]), 200))
            except (TypeError, ValueError):
                limit = 200
            from meuharness.domains.whatsapp_shadow import get_conversation_by_contact, recent_messages

            messages = list(reversed(recent_messages(agent_id, contact_name=contact, limit=limit)))
            conversation = get_conversation_by_contact(agent_id, contact) if contact else None
            self.send_json(200, {
                "agent_id": agent_id,
                "contact": contact,
                "conversation": conversation,
                "messages": messages,
                "count": len(messages),
            })
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/logistics/erp"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent or not _agent_has_logistics_erp(agent):
                self.send_json(404, {"error": "ERP logístico não está habilitado neste agente."})
                return
            from meuharness.domains.logistics_erp import board_snapshot
            self.send_json(200, {"board": board_snapshot(agent_id)})
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/crm"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent or not _agent_has_whatsapp(agent):
                self.send_json(404, {"error": "Agente WhatsApp não encontrado."})
                return
            query = parse_qs(u.query)
            contact = str((query.get("contact") or [""])[0]).strip()
            from meuharness.domains.whatsapp_crm import crm_inbox, crm_snapshot

            payload = {"agent_id": agent_id, "inbox": crm_inbox(agent_id, limit=30)}
            if contact:
                payload["snapshot"] = crm_snapshot(agent_id, contact)
            self.send_json(200, payload)
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent:
                self.send_json(404, {"error": "Agente não encontrado."})
                return
            if not _agent_has_whatsapp(agent):
                self.send_json(404, {"error": "Skill WhatsApp não está habilitada neste agente."})
                return
            from meuharness.domains.whatsapp_shadow import (
                list_inbox,
                list_monitored_contacts,
                pending_outbox,
                runtime_settings,
                sync_state_for_contact,
            )
            from meuharness.domains.whatsapp_sync import discover_sidebar_contacts
            from meuharness.domains.whatsapp_automation import list_jobs

            monitored = list_monitored_contacts(agent_id)
            continuity = {
                row["name"]: sync_state_for_contact(agent_id, row["name"])
                for row in monitored
            }
            self.send_json(200, {
                "runtime": runtime_settings(agent_id),
                "monitored": monitored,
                "continuity": continuity,
                "available": discover_sidebar_contacts(agent_id, limit=100),
                "inbox": list_inbox(agent_id, limit=200),
                "outbox": pending_outbox(agent_id, limit=20),
                "inbound_jobs": list_jobs(agent_id),
            })
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/browser"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent:
                self.send_json(404, {"error": "Agente não encontrado."})
                return
            tools, scopes = _agent_tools_and_scopes(agent)
            if "harness_browser" not in tools:
                self.send_json(404, {"error": "Harness Browser não está habilitado neste agente."})
                return
            if not ({"browser.read", "browser.interact"} & scopes):
                self.send_json(403, {"error": "Agente sem permissão browser.read."})
                return
            self.send_json(200, {"browser": browser_snapshot(agent_id), "can_interact": "browser.interact" in scopes})
            return
        if u.path == "/api/companies":
            self.send_json(200, {"companies": list_companies()})
            return
        if u.path == "/api/company-tasks":
            q = parse_qs(u.query)
            company_id = str((q.get("company_id") or [""])[0]).strip()
            if not company_id:
                self.send_json(400, {"error": "company_id é obrigatório."})
                return
            status = str((q.get("status") or [""])[0]).strip()
            kind = str((q.get("kind") or [""])[0]).strip()
            limit = int((q.get("limit") or ["100"])[0])
            self.send_json(200, {"tasks": list_company_tasks(company_id, status=status, kind=kind, limit=limit)})
            return
        if u.path == "/api/sectors":
            q = parse_qs(u.query)
            company_id = (q.get("company_id") or [None])[0]
            self.send_json(200, {"sectors": list_sectors(company_id)})
            return
        if u.path == "/api/context-items":
            q = parse_qs(u.query)
            scope_type = (q.get("scope_type") or [None])[0]
            scope_id = (q.get("scope_id") or [None])[0]
            self.send_json(200, {"items": list_context_items(scope_type, scope_id)})
            return
        if u.path == "/api/projects":
            q = parse_qs(u.query)
            company_id = (q.get("company_id") or [None])[0]
            self.send_json(200, {"projects": list_projects(company_id)})
            return
        if u.path == "/api/agent-context-bindings":
            q = parse_qs(u.query)
            agent_id = (q.get("agent_id") or [None])[0]
            self.send_json(200, {"bindings": list_agent_bindings(agent_id)})
            return
        if u.path == "/api/conversations":
            q = parse_qs(u.query)
            aid = (q.get("agent_id") or [None])[0]
            self.send_json(200, {"conversations": list_conversations(aid)})
            return
        if u.path.startswith("/api/conversations/"):
            conversation_id = u.path.split("/")[3]
            conversation = get_conversation(conversation_id)
            if not conversation:
                self.send_json(404, {"error": "Conversa não encontrada."})
                return
            self.send_json(200, {"conversation": conversation})
            return
        if u.path == "/api/automations":
            self.send_json(200, {"automations": list_automations()})
            return
        if u.path.startswith("/api/automations/"):
            automation_id = u.path.split("/")[3]
            automation = get_automation(automation_id)
            if not automation:
                self.send_json(404, {"error": "Automação não encontrada."})
                return
            self.send_json(200, {"automation": automation})
            return
        if u.path == "/api/approvals":
            q = parse_qs(u.query)
            status = (q.get("status") or [None])[0]
            self.send_json(200, {"approvals": list_approvals(status)})
            return
        if u.path == "/api/workflows":
            self.send_json(200, {"workflows": list_workflows()})
            return
        if u.path.startswith("/api/workflows/") and u.path.endswith("/runs"):
            workflow_id = u.path.split("/")[3]
            self.send_json(200, {"runs": list_workflow_runs(workflow_id)})
            return
        if u.path.startswith("/api/workflows/"):
            workflow = get_workflow(u.path.split("/")[3])
            if not workflow:
                self.send_json(404, {"error": "Workflow não encontrado."})
                return
            self.send_json(200, {"workflow": workflow, "validation": validate_workflow(workflow)})
            return
        if u.path == "/api/memory":
            q = parse_qs(u.query)
            agent_id = str((q.get("agent_id") or [""])[0])
            prompt = str((q.get("q") or [""])[0])
            self.send_json(200, {"memories": recall(agent_id, prompt, limit=20) if agent_id else []})
            return
        if u.path == "/api/storage":
            self.send_json(200, {"backend": "sqlite", "path": str(DB_FILE), "exists": DB_FILE.is_file()})
            return
        if u.path == "/api/system-usage":
            self.send_json(200, _actis_system_usage())
            return
        if u.path == "/api/runs":
            q = parse_qs(u.query)
            aid = (q.get("agent_id") or [None])[0]
            self.send_json(200, {"runs": list_runs(aid)})
            return
        if u.path.startswith("/api/runs/") and u.path.endswith("/checkpoints"):
            run_id = u.path.split("/")[3]
            self.send_json(200, {"run_id": run_id, "checkpoints": list_checkpoints(run_id)})
            return
        if u.path == "/api/tasks":
            q = parse_qs(u.query)
            active_only = str((q.get("active") or ["0"])[0]).lower() in {"1", "true", "yes"}
            limit = int((q.get("limit") or ["200"])[0])
            agent_id = str((q.get("agent_id") or [""])[0]).strip() or None
            self.send_json(200, {"tasks": list_tasks(agent_id=agent_id, active_only=active_only, limit=limit)})
            return
        if u.path == "/api/world/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seed = list_events(limit=1)
            last_id = seed[0].get("id") if seed else None
            started = time.monotonic()
            try:
                self.wfile.write(b"event: ready\ndata: {}\n\n")
                self.wfile.flush()
                while time.monotonic() - started < 25:
                    current = list_events(limit=80)
                    fresh = []
                    if current and last_id:
                        for event in current:
                            if event.get("id") == last_id:
                                break
                            fresh.append(event)
                    if current:
                        last_id = current[0].get("id")
                    for event in reversed(fresh):
                        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
                        self.wfile.write(b"event: actis\ndata: " + payload + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.35)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if u.path == "/api/events":
            q = parse_qs(u.query)
            limit = int((q.get("limit") or ["200"])[0])
            event_type = (q.get("type") or [None])[0]
            run_id = (q.get("run_id") or [None])[0]
            self.send_json(200, {"events": list_events(limit=limit, event_type=event_type, run_id=run_id)})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/runs/") and u.path.endswith("/resume"):
                run_id = u.path.split("/")[3]
                result = asyncio.run(resume_run(run_id, env_file=self.env_file))
                self.send_json(200, {"ok": True, "run_id": result.run_id, "resumed_from": run_id, "text": result.text, "model": result.model})
                return
            if u.path.startswith("/api/runs/") and u.path.endswith("/cancel"):
                run_id = u.path.split("/")[3]
                signalled = cancel_active_run(run_id)
                if not signalled:
                    changed = cancel_run_record(run_id, "Execução cancelada/órfã encerrada pelo usuário.")
                    if not changed:
                        self.send_json(404, {"error": "Run ativa não encontrada."})
                        return
                self.send_json(200, {"ok": True, "run_id": run_id, "signalled": signalled})
                return
            if u.path == "/api/connectors":
                self.send_json(201, {"connector": save_connector(self.read_json())})
                return
            if u.path.startswith("/api/connectors/") and u.path.endswith("/test"):
                connector_id = u.path.split("/")[3]
                self.send_json(200, {"result": test_connector(connector_id, self.env_file)})
                return
            if u.path == "/api/provider-connections/test":
                result = self.router_dashboard_post("/api/providers/test-batch", {"mode": "all"})
                if result is None:
                    self.send_json(
                        401, {"error": "Não foi possível usar a sessão do 9Router neste navegador."}
                    )
                    return
                self.send_json(200, {**result, "source": "9router_native_batch_test"})
                return
            if u.path == "/api/model-probe":
                data = self.read_json()
                model = str(data.get("model") or "").strip()
                settings = NineRouterSettings.from_env(self.env_file, model=model or None, timeout_s=30)
                result = asyncio.run(NineRouterGateway(settings).complete("Reply exactly ACTIS_PROBE_OK", max_output_tokens=20))
                self.send_json(200, {"ok": "ACTIS_PROBE_OK" in result.text, "model": result.model, "text": result.text})
                return
            if u.path == "/api/approvals":
                data = self.read_json()
                self.send_json(201, {"approval": request_approval(str(data.get("agent_id") or ""), str(data.get("scope") or ""), str(data.get("reason") or ""))})
                return
            if u.path.startswith("/api/approvals/") and u.path.endswith("/resolve"):
                approval_id = u.path.split("/")[3]
                data = self.read_json()
                approval = resolve_approval(approval_id, bool(data.get("approved")))
                self.send_json(200, {"approval": approval})
                return
            if u.path.startswith("/api/approvals/") and u.path.endswith("/revoke"):
                approval_id = u.path.split("/")[3]
                self.send_json(200, {"approval": revoke_approval(approval_id)})
                return
            if u.path == "/api/workflows":
                self.send_json(201, {"workflow": save_workflow(self.read_json())})
                return
            if u.path.startswith("/api/workflows/") and u.path.endswith("/run"):
                workflow_id = u.path.split("/")[3]
                data = self.read_json()
                result = asyncio.run(run_workflow(workflow_id, str(data.get("input") or ""), self.env_file))
                self.send_json(200, {"run": result})
                return
            if u.path.startswith("/api/workflows/") and u.path.endswith("/validate"):
                workflow_id = u.path.split("/")[3]
                workflow = get_workflow(workflow_id)
                if not workflow:
                    raise ValueError("Workflow não encontrado.")
                self.send_json(200, {"errors": validate_workflow(workflow)})
                return
            if u.path == "/api/automations":
                self.send_json(201, {"automation": create_automation(self.read_json())})
                return
            if u.path == "/api/channels":
                self.send_json(201, {"channel": create_channel(self.read_json())})
                return
            if u.path.startswith("/api/channels/") and u.path.endswith("/members"):
                channel_id = u.path.split("/")[3]
                data = self.read_json()
                members = set_channel_members(channel_id, list(data.get("members") or []))
                self.send_json(200, {"members": members, "channel": get_channel(channel_id)})
                return
            if u.path.startswith("/api/channels/") and u.path.endswith("/messages"):
                channel_id = u.path.split("/")[3]
                self.send_json(201, {"message": create_channel_message(channel_id, self.read_json())})
                return
            if u.path.startswith("/api/channels/") and u.path.endswith("/invoke"):
                channel_id = u.path.split("/")[3]
                data = self.read_json()
                agent_id = str(data.get("agent_id") or "").strip()
                content = str(data.get("content") or "").strip()
                agent = get_agent(agent_id)
                if not agent or not content:
                    raise ValueError("Agente e mensagem são obrigatórios.")
                create_channel_message(channel_id, {"content": content, "author_name": "Você"})
                prior = list_channel_messages(channel_id, limit=18)[:-1]
                history = [
                    {"role": "assistant" if item.get("author_type") == "agent" and item.get("author_id") == agent_id else "user",
                     "content": f"{item.get('author_name')}: {item.get('content')}"}
                    for item in prior[-15:]
                ]
                result = asyncio.run(execute_agent(agent_id, content, history=history, source="direct", env_file=self.env_file))
                reply = create_channel_message(channel_id, {
                    "content": result.text, "author_type": "agent", "author_id": agent_id,
                    "author_name": str(agent.get("name") or agent_id), "kind": "message",
                })
                self.send_json(200, {"message": reply, "run_id": result.run_id, "model": result.model})
                return
            if u.path == "/api/agents":
                self.send_json(201, {"agent": create_agent(self.read_json())})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/logistics/erp"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or not _agent_has_logistics_erp(agent):
                    self.send_json(404, {"error": "ERP logístico não está habilitado neste agente."})
                    return
                data = self.read_json()
                action = str(data.get("action") or "").strip().lower()
                from meuharness.domains.logistics_erp import (
                    add_pending, board_snapshot, create_order, delete_order, set_pending, update_order,
                )
                if action == "create":
                    result = create_order(agent_id, data)
                elif action == "update":
                    result = update_order(agent_id, str(data.get("order_id") or ""), data)
                elif action == "delete":
                    result = delete_order(agent_id, str(data.get("order_id") or ""))
                elif action == "add_pending":
                    result = add_pending(agent_id, str(data.get("order_id") or ""), str(data.get("title") or ""), str(data.get("due_date") or ""))
                elif action == "set_pending":
                    result = set_pending(agent_id, str(data.get("order_id") or ""), str(data.get("pending_id") or ""), str(data.get("status") or "done"))
                else:
                    raise ValueError("Ação inválida para o ERP logístico")
                self.send_json(200, {"result": result, "board": board_snapshot(agent_id)})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/contacts"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or not _agent_has_whatsapp(agent):
                    self.send_json(404, {"error": "Agente WhatsApp não encontrado."})
                    return
                from meuharness.domains.whatsapp_shadow import upsert_contact

                data = self.read_json()
                contact = str(data.get("contact") or "").strip()
                if not contact:
                    raise ValueError("Contato é obrigatório.")
                row = upsert_contact(
                    agent_id,
                    contact,
                    external_id=str(data.get("phone") or "").strip() or None,
                    monitored=bool(data.get("enabled", True)),
                    sync_history=bool(data.get("sync_history", False)),
                )
                self.send_json(200, {"contact": row})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/crm"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or not _agent_has_whatsapp(agent):
                    self.send_json(404, {"error": "Agente WhatsApp não encontrado."})
                    return
                data = self.read_json()
                contact = str(data.get("contact") or "").strip()
                if not contact:
                    raise ValueError("Contato é obrigatório.")
                action = str(data.get("action") or "update").strip().lower()
                from meuharness.domains.whatsapp_crm import add_item, triage_conversation, update_crm

                if action == "triage":
                    result = triage_conversation(agent_id, contact, env_file=self.env_file)
                elif action in {"task", "pending", "order"}:
                    result = add_item(agent_id, contact, action, data)
                else:
                    result = update_crm(agent_id, contact, data)
                self.send_json(200, result)
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/sync"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or not _agent_has_whatsapp(agent):
                    self.send_json(404, {"error": "Agente WhatsApp não encontrado."})
                    return
                from meuharness.domains.whatsapp_sync import sync_once

                data = self.read_json()
                contact = str(data.get("contact") or "").strip() or None
                emit_inbound_events = bool(data.get("emit_inbound_events", False))
                self.send_json(200, {"sync": sync_once(
                    agent_id, contact_name=contact, emit_inbound_events=emit_inbound_events,
                )})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/automation"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or not _agent_has_whatsapp(agent):
                    self.send_json(404, {"error": "Agente WhatsApp não encontrado."})
                    return
                from meuharness.domains.whatsapp_shadow import set_automation_enabled

                data = self.read_json()
                self.send_json(200, {"runtime": set_automation_enabled(agent_id, bool(data.get("enabled")))})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/browser/action"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent:
                    self.send_json(404, {"error": "Agente não encontrado."})
                    return
                tools, scopes = _agent_tools_and_scopes(agent)
                if "harness_browser" not in tools:
                    self.send_json(404, {"error": "Harness Browser não está habilitado neste agente."})
                    return
                if "browser.interact" not in scopes:
                    self.send_json(403, {"error": "Agente sem permissão browser.interact."})
                    return
                data = self.read_json()
                self.send_json(200, browser_action(agent_id, str(data.get("action") or ""), data))
                return
            if u.path == "/api/context-items":
                self.send_json(201, {"item": create_context_item(self.read_json())})
                return
            if u.path == "/api/projects":
                self.send_json(201, {"project": create_project(self.read_json())})
                return
            if u.path == "/api/conversations":
                data = self.read_json()
                agent_id = str(data.get("agent_id") or "").strip()
                self.send_json(201, {"conversation": create_conversation(agent_id, data.get("title"))})
                return
            if u.path.startswith("/api/conversations/") and u.path.endswith("/messages"):
                conversation_id = u.path.split("/")[3]
                conversation = get_conversation(conversation_id)
                if not conversation:
                    raise ValueError("Conversa não encontrada.")
                agent_id = str(conversation.get("agent_id") or "")
                data = self.read_json()
                raw_attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
                attachments = normalize_chat_attachments(raw_attachments, conversation_id)
                prompt = str(data.get("content") or "").strip()
                if not prompt and attachments:
                    prompt = "Analise os arquivos anexados."
                if not prompt:
                    raise ValueError("Mensagem vazia.")
                source = str(data.get("source") or "user").strip().lower()
                if source not in {"user", "direct"}:
                    source = "user"
                previous_messages = [
                    {"role": m.get("role"), "content": history_content_with_attachment_refs(m)}
                    for m in list(conversation.get("messages") or [])
                    if m.get("role") in {"user", "assistant"} and m.get("status") != "error"
                ][-16:]
                user_message = append_conversation_message(conversation_id, "user", prompt, attachments=attachments)
                history = previous_messages + [{"role": "user", "content": prompt}]
                try:
                    result = asyncio.run(
                        execute_agent(
                            agent_id,
                            prompt,
                            history=history,
                            source=source,
                            conversation_id=conversation_id,
                            agent_refs=data.get("agent_refs") if isinstance(data.get("agent_refs"), list) else None,
                            attachments=attachments,
                            env_file=self.env_file,
                        )
                    )
                    artifact_attachments = list_run_attachments(result.run_id)
                    assistant_message = append_conversation_message(
                        conversation_id, "assistant", result.text, run_id=result.run_id,
                        attachments=artifact_attachments,
                    )
                    self.send_json(
                        200,
                        {
                            "text": result.text,
                            "model": result.model,
                            "elapsed_ms": result.elapsed_ms,
                            "run_id": result.run_id,
                            "user_message": user_message,
                            "assistant_message": assistant_message,
                            "artifacts": artifact_attachments,
                            "conversation": get_conversation(conversation_id),
                        },
                    )
                    return
                except Exception as exc:
                    append_conversation_message(
                        conversation_id,
                        "assistant",
                        "Erro: " + str(exc),
                        run_id=getattr(exc, "actis_run_id", None),
                        status="error",
                    )
                    raise
            if u.path == "/api/companies":
                self.send_json(201, {"company": create_company(self.read_json())})
                return
            if u.path == "/api/sectors":
                self.send_json(201, {"sector": create_sector(self.read_json())})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/chat"):
                agent_id = u.path.split("/")[3]
                with _GLOBAL_CHAT_BUSY_LOCK:
                    if agent_id in _GLOBAL_CHAT_BUSY_AGENTS:
                        self.send_json(
                            409,
                            {"error": "Este agente já possui uma requisição do chat global em andamento."},
                        )
                        return
                    _GLOBAL_CHAT_BUSY_AGENTS.add(agent_id)
                try:
                    data = self.read_json()
                    history = data.get("history") or []
                    if not isinstance(history, list) or not history:
                        raise ValueError("Histórico vazio.")
                    prompt = str(history[-1].get("content") or "").strip()
                    result = asyncio.run(
                        execute_agent(
                            agent_id,
                            prompt,
                            history=history,
                            source=str(data.get("source") or "user"),
                            env_file=self.env_file,
                        )
                    )
                    self.send_json(
                        200,
                        {
                            "text": result.text,
                            "model": result.model,
                            "elapsed_ms": result.elapsed_ms,
                            "run_id": result.run_id,
                            "artifacts": list_run_attachments(result.run_id),
                        },
                    )
                    return
                finally:
                    with _GLOBAL_CHAT_BUSY_LOCK:
                        _GLOBAL_CHAT_BUSY_AGENTS.discard(agent_id)
            self.send_error(404)
        except (HarnessError, ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            self.send_json(500, {"error": "falha inesperada no servidor local"})

    def do_PUT(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/channel-messages/"):
                message_id = u.path.split("/")[3]
                message = update_channel_message(message_id, self.read_json())
                if not message:
                    self.send_json(404, {"error": "Mensagem não encontrada ou sem alterações."})
                    return
                self.send_json(200, {"message": message})
                return
            if u.path.startswith("/api/agent-context-bindings/"):
                agent_id = u.path.split("/")[3]
                data = self.read_json()
                self.send_json(200, {"bindings": set_agent_project_bindings(agent_id, list(data.get("project_ids") or []))})
                return
            if u.path.startswith("/api/connectors/"):
                connector_id = u.path.split("/")[3]
                data = self.read_json()
                data["id"] = connector_id
                self.send_json(200, {"connector": save_connector(data)})
                return
            if u.path.startswith("/api/workflows/"):
                workflow_id = u.path.split("/")[3]
                self.send_json(200, {"workflow": save_workflow(self.read_json(), workflow_id)})
                return
            if u.path.startswith("/api/automations/"):
                automation_id = u.path.split("/")[3]
                self.send_json(200, {"automation": update_automation(automation_id, self.read_json())})
                return
            if u.path.startswith("/api/agents/"):
                agent_id = u.path.split("/")[3]
                self.send_json(200, {"agent": update_agent(agent_id, self.read_json())})
                return
            if u.path.startswith("/api/companies/"):
                company_id = u.path.split("/")[3]
                self.send_json(200, {"company": update_company(company_id, self.read_json())})
                return
            if u.path.startswith("/api/sectors/"):
                sector_id = u.path.split("/")[3]
                self.send_json(200, {"sector": update_sector(sector_id, self.read_json())})
                return
            self.send_error(404)
        except (ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            self.send_json(500, {"error": "falha inesperada no servidor local"})


    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/channel-messages/"):
                message_id = u.path.split("/")[3]
                if not delete_channel_message(message_id):
                    self.send_json(404, {"error": "Mensagem não encontrada."})
                    return
                self.send_json(200, {"ok": True})
                return
            if u.path.startswith("/api/channels/"):
                channel_id = u.path.split("/")[3]
                if not delete_channel(channel_id):
                    self.send_json(400, {"error": "Canal não encontrado ou protegido."})
                    return
                self.send_json(200, {"ok": True})
                return
            if u.path.startswith("/api/connectors/"):
                connector_id = u.path.split("/")[3]
                if not delete_connector(connector_id):
                    self.send_json(400, {"error": "Connector não encontrado ou protegido."})
                    return
                self.send_json(200, {"ok": True})
                return
            if u.path.startswith("/api/context-items/"):
                item_id = u.path.split("/")[3]
                if not delete_context_item(item_id):
                    self.send_json(404, {"error": "Contexto não encontrado."})
                    return
                self.send_json(200, {"ok": True})
                return
            if u.path.startswith("/api/workflows/"):
                workflow_id = u.path.split("/")[3]
                if not delete_workflow(workflow_id):
                    self.send_json(404, {"error": "Workflow não encontrado."})
                    return
                self.send_json(200, {"ok": True})
                return
            if u.path.startswith("/api/automations/"):
                automation_id = u.path.split("/")[3]
                if not delete_automation(automation_id):
                    self.send_json(404, {"error": "Automação não encontrada."})
                    return
                self.send_json(200, {"ok": True})
                return
            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--embedded-scheduler", action="store_true", help="Run scheduler inside web process (legacy/dev mode).")
    args = parser.parse_args()
    Handler.env_file = args.env_file
    interrupt_stale_running_runs()
    reconcile_tasks_with_runs()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"ACTIS GEN: {url}", flush=True)
    automation_stop = None
    if args.embedded_scheduler:
        automation_stop, _automation_thread = start_scheduler(url, env_file=args.env_file)
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if automation_stop is not None:
            automation_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
