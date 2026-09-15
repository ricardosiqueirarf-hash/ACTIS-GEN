"""ACTIS GEN local browser UI and API."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_GLOBAL_CHAT_BUSY_LOCK = threading.Lock()
_GLOBAL_CHAT_BUSY_AGENTS: set[str] = set()

from meuharness import HarnessError
from meuharness.agents import (
    append_conversation_message,
    cancel_run_record,
    cancel_stale_running_runs,
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
from meuharness.contexts import (
    create_context_item,
    create_project,
    delete_context_item,
    list_agent_bindings,
    list_context_items,
    list_projects,
    set_agent_project_bindings,
)
from meuharness.event_bus import list_events
from meuharness.execution_service import cancel_active_run, execute_agent
from meuharness.memory import recall
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.storage import DB_FILE
from meuharness.tasks import list_tasks
from meuharness.tool_registry import list_tool_catalog, normalize_scopes, scopes_for_tools
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


class Handler(BaseHTTPRequestHandler):
    env_file: str | None = None

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
                    if model in health:
                        health[model]["status"] = (
                            "working" if run.get("status") == "completed" else "failing"
                        )
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
        if u.path == "/api/tools":
            self.send_json(200, {"tools": list_tool_catalog()})
            return
        if u.path == "/api/agents":
            self.send_json(200, {"agents": list_agents()})
            return
        if u.path.startswith("/api/agents/") and u.path.endswith("/browser"):
            agent_id = u.path.split("/")[3]
            agent = get_agent(agent_id)
            if not agent or "harness_browser" not in set(agent.get("tools") or []):
                self.send_json(404, {"error": "Harness Browser não está habilitado neste agente."})
                return
            scopes = set(normalize_scopes(agent.get("permissions") or [])) if "permissions" in agent else set(scopes_for_tools(agent.get("tools") or []))
            if not ({"browser.read", "browser.interact"} & scopes):
                self.send_json(403, {"error": "Agente sem permissão browser.read."})
                return
            self.send_json(200, {"browser": browser_snapshot(agent_id), "can_interact": "browser.interact" in scopes})
            return
        if u.path == "/api/companies":
            self.send_json(200, {"companies": list_companies()})
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
        if u.path == "/api/runs":
            q = parse_qs(u.query)
            aid = (q.get("agent_id") or [None])[0]
            self.send_json(200, {"runs": list_runs(aid)})
            return
        if u.path == "/api/tasks":
            q = parse_qs(u.query)
            active_only = str((q.get("active") or ["0"])[0]).lower() in {"1", "true", "yes"}
            limit = int((q.get("limit") or ["200"])[0])
            self.send_json(200, {"tasks": list_tasks(active_only=active_only, limit=limit)})
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
            if u.path == "/api/agents":
                self.send_json(201, {"agent": create_agent(self.read_json())})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/browser/action"):
                agent_id = u.path.split("/")[3]
                agent = get_agent(agent_id)
                if not agent or "harness_browser" not in set(agent.get("tools") or []):
                    self.send_json(404, {"error": "Harness Browser não está habilitado neste agente."})
                    return
                scopes = set(normalize_scopes(agent.get("permissions") or [])) if "permissions" in agent else set(scopes_for_tools(agent.get("tools") or []))
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
                attachments = []
                for item in raw_attachments[:4]:
                    if not isinstance(item, dict):
                        continue
                    media_type = str(item.get("media_type") or "").strip().lower()
                    data_url = str(item.get("data_url") or "").strip()
                    name = str(item.get("name") or "imagem").strip()[:120]
                    if not media_type.startswith("image/") or not data_url.startswith("data:image/"):
                        continue
                    if len(data_url) > 8_500_000:
                        raise ValueError("Imagem muito grande. Limite: 6 MB por imagem.")
                    attachments.append({"name": name, "media_type": media_type, "data_url": data_url})
                prompt = str(data.get("content") or "").strip()
                if not prompt and attachments:
                    prompt = "Analise a imagem anexada."
                if not prompt:
                    raise ValueError("Mensagem vazia.")
                source = str(data.get("source") or "user").strip().lower()
                if source not in {"user", "direct"}:
                    source = "user"
                previous_messages = [
                    {"role": m.get("role"), "content": m.get("content")}
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
                    assistant_message = append_conversation_message(
                        conversation_id, "assistant", result.text, run_id=result.run_id
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
            if u.path.startswith("/api/agent-context-bindings/"):
                agent_id = u.path.split("/")[3]
                data = self.read_json()
                self.send_json(200, {"bindings": set_agent_project_bindings(agent_id, list(data.get("project_ids") or []))})
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
    cancel_stale_running_runs()
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
