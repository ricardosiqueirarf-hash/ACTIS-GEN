"""ACTIS GEN embedded Android API server.

API-compatible subset of web.py which deliberately avoids desktop/MAF imports.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
from meuharness.android_execution import (
    cancel_active_run_android,
    execute_agent_android,
    list_models_http,
    provider_status,
    save_provider,
)
from meuharness.android_workflows import run_workflow_android
from meuharness.approvals import list_approvals, request_approval, resolve_approval, revoke_approval
from meuharness.channels import (
    create_channel, create_channel_message, delete_channel, delete_channel_message,
    get_channel, list_channel_messages, list_channels, set_channel_members, update_channel_message,
)
from meuharness.company_bus import list_company_tasks
from meuharness.skills import list_skill_catalog, runtime_features_for_skills
from meuharness.automations import create_automation, delete_automation, get_automation, list_automations, update_automation
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
from meuharness.memory import recall
from meuharness.storage import DB_FILE, load_collection, save_collection
from meuharness.tasks import list_tasks
from meuharness.tool_registry import list_tool_catalog
from meuharness.workflows import delete_workflow, get_workflow, list_workflow_runs, list_workflows, save_workflow, validate_workflow

INDEX = Path(__file__).with_name("web_assets") / "index.html"
_GLOBAL_CHAT_BUSY_LOCK = threading.Lock()
_GLOBAL_CHAT_BUSY_AGENTS: set[str] = set()


def _runtime_features(agent):
    return set(runtime_features_for_skills(list(agent.get("skills") or [])))

def _android_system_usage():
    ram=0
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                ram=int(line.split()[1])*1024
                break
    except Exception:
        pass
    return {"cpu_percent":0.0,"ram_bytes":ram,"ram_mb":round(ram/1048576,1),"processes":1,"runtime":"android-embedded"}

def _save_connector_local(data):
    items=load_collection("connectors")
    cid=str(data.get("id") or "").strip() or f"connector-{int(time.time()*1000)}"
    row={**data,"id":cid,"name":str(data.get("name") or cid).strip(),"enabled":bool(data.get("enabled",True))}
    old=next((x for x in items if x.get("id")==cid),None)
    if old: items[items.index(old)]=row
    else: items.append(row)
    save_collection("connectors",items)
    return row

class AndroidHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def end_headers(self) -> None:
        origin = str(self.headers.get("Origin") or "").strip()
        if origin in {"https://localhost", "http://localhost", "capacitor://localhost"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0") or 0)
        value = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(value, dict):
            raise TypeError("JSON inválido")
        return value

    def do_GET(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path == "/":
                raw = INDEX.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if u.path == "/api/android-runtime":
                self.send_json(200, {"runtime": "android-embedded", "core": "online", "provider": provider_status()})
                return
            if u.path == "/api/android-provider":
                self.send_json(200, {"provider": provider_status()})
                return
            if u.path == "/api/models":
                models = list_models_http()
                cfg = provider_status()
                self.send_json(200, {"models": models, "selected": cfg.get("model") or "", "runtime": "android-embedded"})
                return
            if u.path == "/api/model-health":
                models = list_models_http()
                latest: dict[str, dict] = {}
                for run in list_runs():
                    model = str(run.get("model") or "")
                    if model and model not in latest:
                        latest[model] = run
                rows = []
                for model in models:
                    last = latest.get(model)
                    rows.append({"model": model, "status": "working" if last and last.get("status") == "completed" else "unknown", "source": "last_run" if last else "android_catalog"})
                self.send_json(200, {"models": rows, "token_cost": 0})
                return
            if u.path == "/api/provider-connections":
                cfg = provider_status()
                self.send_json(200, {"connections": ([{"id": "android-provider", "provider": "openai-compatible", "name": cfg.get("base_url") or "Android provider", "auth_type": "api_key", "active": bool(cfg.get("configured")), "test_status": "active" if cfg.get("configured") else "unavailable", "last_error": None}] if cfg.get("configured") else [])})
                return
            if u.path == "/api/connectors":
                self.send_json(200,{"connectors":load_collection("connectors")}); return
            if u.path == "/api/skills":
                self.send_json(200,{"skills":list_skill_catalog()}); return
            if u.path == "/api/channels":
                self.send_json(200,{"channels":list_channels()}); return
            if u.path.startswith("/api/channels/") and u.path.endswith("/messages"):
                cid=u.path.split("/")[3]; channel=get_channel(cid)
                self.send_json(200 if channel else 404,{"channel":channel,"messages":list_channel_messages(cid) if channel else []}); return
            if u.path.startswith("/api/channels/"):
                channel=get_channel(u.path.split("/")[3])
                self.send_json(200 if channel else 404,{"channel":channel} if channel else {"error":"Canal não encontrado."}); return
            if u.path == "/api/tools":
                tools = []
                for item in list_tool_catalog():
                    row = dict(item)
                    if row.get("id") in {"harness_browser", "harness_computer"}:
                        row["status"] = "desktop_only"
                    tools.append(row)
                self.send_json(200, {"tools": tools})
                return
            if u.path == "/api/agents":
                self.send_json(200, {"agents": list_agents()})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/logistics/erp"):
                aid=u.path.split("/")[3]; agent=get_agent(aid)
                if not agent or "logistics.erp.local" not in _runtime_features(agent): self.send_json(404,{"error":"ERP logístico não está habilitado neste agente."}); return
                from meuharness.domains.logistics_erp import board_snapshot
                self.send_json(200,{"board":board_snapshot(aid)}); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/messages"):
                aid=u.path.split("/")[3]; agent=get_agent(aid)
                if not agent or "whatsapp.local" not in _runtime_features(agent): self.send_json(404,{"error":"Agente WhatsApp não encontrado."}); return
                q=parse_qs(u.query); contact=str((q.get("contact") or [""])[0]).strip() or None
                from meuharness.domains.whatsapp_shadow import get_conversation_by_contact, recent_messages
                msgs=list(reversed(recent_messages(aid,contact_name=contact,limit=200)))
                self.send_json(200,{"agent_id":aid,"contact":contact,"conversation":get_conversation_by_contact(aid,contact) if contact else None,"messages":msgs,"count":len(msgs)}); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/crm"):
                aid=u.path.split("/")[3]; q=parse_qs(u.query); contact=str((q.get("contact") or [""])[0]).strip()
                from meuharness.domains.whatsapp_crm import crm_inbox, crm_snapshot
                payload={"agent_id":aid,"inbox":crm_inbox(aid,limit=30)}
                if contact: payload["snapshot"]=crm_snapshot(aid,contact)
                self.send_json(200,payload); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp"):
                self.send_json(501,{"error":"WhatsApp transport permanece desktop-only no Core Android."}); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/browser"):
                self.send_json(501, {"error": "Harness Browser desktop não está disponível no Core Android embutido."})
                return
            if u.path == "/api/companies":
                self.send_json(200, {"companies": list_companies()})
                return
            if u.path == "/api/company-tasks":
                q=parse_qs(u.query); company_id=str((q.get("company_id") or [""])[0]).strip()
                if not company_id: self.send_json(400,{"error":"company_id é obrigatório."}); return
                self.send_json(200,{"tasks":list_company_tasks(company_id,status=str((q.get("status") or [""])[0]),kind=str((q.get("kind") or [""])[0]),limit=int((q.get("limit") or ["100"])[0]))}); return
            if u.path == "/api/sectors":
                q = parse_qs(u.query)
                self.send_json(200, {"sectors": list_sectors((q.get("company_id") or [None])[0])})
                return
            if u.path == "/api/context-items":
                q = parse_qs(u.query)
                self.send_json(200, {"items": list_context_items((q.get("scope_type") or [None])[0], (q.get("scope_id") or [None])[0])})
                return
            if u.path == "/api/projects":
                q = parse_qs(u.query)
                self.send_json(200, {"projects": list_projects((q.get("company_id") or [None])[0])})
                return
            if u.path == "/api/agent-context-bindings":
                q = parse_qs(u.query)
                self.send_json(200, {"bindings": list_agent_bindings((q.get("agent_id") or [None])[0])})
                return
            if u.path == "/api/conversations":
                q = parse_qs(u.query)
                self.send_json(200, {"conversations": list_conversations((q.get("agent_id") or [None])[0])})
                return
            if u.path.startswith("/api/conversations/"):
                conversation = get_conversation(u.path.split("/")[3])
                if not conversation:
                    self.send_json(404, {"error": "Conversa não encontrada."})
                else:
                    self.send_json(200, {"conversation": conversation})
                return
            if u.path == "/api/automations":
                self.send_json(200, {"automations": list_automations()})
                return
            if u.path.startswith("/api/automations/"):
                automation = get_automation(u.path.split("/")[3])
                if not automation:
                    self.send_json(404, {"error": "Automação não encontrada."})
                else:
                    self.send_json(200, {"automation": automation})
                return
            if u.path == "/api/approvals":
                q = parse_qs(u.query)
                self.send_json(200, {"approvals": list_approvals((q.get("status") or [None])[0])})
                return
            if u.path == "/api/workflows":
                self.send_json(200, {"workflows": list_workflows()})
                return
            if u.path.startswith("/api/workflows/") and u.path.endswith("/runs"):
                self.send_json(200, {"runs": list_workflow_runs(u.path.split("/")[3])})
                return
            if u.path.startswith("/api/workflows/"):
                workflow = get_workflow(u.path.split("/")[3])
                if not workflow:
                    self.send_json(404, {"error": "Workflow não encontrado."})
                else:
                    self.send_json(200, {"workflow": workflow, "validation": validate_workflow(workflow)})
                return
            if u.path == "/api/memory":
                q = parse_qs(u.query)
                agent_id = str((q.get("agent_id") or [""])[0])
                prompt = str((q.get("q") or [""])[0])
                self.send_json(200, {"memories": recall(agent_id, prompt, limit=20) if agent_id else []})
                return
            if u.path == "/api/storage":
                self.send_json(200, {"backend": "sqlite", "path": str(DB_FILE), "exists": DB_FILE.is_file(), "runtime": "android-embedded"})
                return
            if u.path == "/api/system-usage":
                self.send_json(200,_android_system_usage()); return
            if u.path == "/api/runs":
                q = parse_qs(u.query)
                self.send_json(200, {"runs": list_runs((q.get("agent_id") or [None])[0])})
                return
            if u.path == "/api/tasks":
                q = parse_qs(u.query)
                active = str((q.get("active") or ["0"])[0]).lower() in {"1", "true", "yes"}
                limit = int((q.get("limit") or ["200"])[0])
                self.send_json(200, {"tasks": list_tasks(active_only=active, limit=limit)})
                return
            if u.path == "/api/events":
                q = parse_qs(u.query)
                self.send_json(200, {"events": list_events(limit=int((q.get("limit") or ["200"])[0]), event_type=(q.get("type") or [None])[0], run_id=(q.get("run_id") or [None])[0])})
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
                            raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            self.wfile.write(b"event: actis\ndata: " + raw + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.35)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            self.send_json(404, {"error": "Endpoint não encontrado no Core Android."})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path == "/api/android-provider":
                self.send_json(200, {"provider": save_provider(self.read_json())})
                return
            if u.path.startswith("/api/runs/") and u.path.endswith("/cancel"):
                run_id = u.path.split("/")[3]
                changed = cancel_active_run_android(run_id) or cancel_run_record(run_id, "Execução cancelada pelo usuário.")
                self.send_json(200 if changed else 404, {"ok": bool(changed), "run_id": run_id, "signalled": bool(changed)})
                return
            if u.path == "/api/model-probe":
                data = self.read_json()
                model = str(data.get("model") or "").strip()
                agent = get_agent("general")
                previous = dict(agent) if agent else None
                if agent and model:
                    update_agent("general", {"model": model})
                try:
                    result = asyncio.run(execute_agent_android("general", "Reply exactly ACTIS_PROBE_OK", source="direct"))
                finally:
                    if previous is not None and model:
                        update_agent("general", {"model": previous.get("model") or ""})
                self.send_json(200, {"ok": "ACTIS_PROBE_OK" in result.text, "model": result.model, "text": result.text})
                return
            if u.path == "/api/provider-connections/test":
                self.send_json(200, {"ok": bool(provider_status().get("configured")), "source": "android_embedded_provider", "provider": provider_status()})
                return
            if u.path == "/api/connectors":
                self.send_json(201,{"connector":_save_connector_local(self.read_json())}); return
            if u.path.startswith("/api/connectors/") and u.path.endswith("/test"):
                self.send_json(200,{"result":{"ok":False,"status":"android_limited","connector_id":u.path.split("/")[3],"detail":"Teste avançado permanece no Core desktop."}}); return
            if u.path == "/api/approvals":
                data = self.read_json()
                self.send_json(201, {"approval": request_approval(str(data.get("agent_id") or ""), str(data.get("scope") or ""), str(data.get("reason") or ""))})
                return
            if u.path.startswith("/api/approvals/") and u.path.endswith("/resolve"):
                data = self.read_json()
                self.send_json(200, {"approval": resolve_approval(u.path.split("/")[3], bool(data.get("approved")))})
                return
            if u.path.startswith("/api/approvals/") and u.path.endswith("/revoke"):
                self.send_json(200, {"approval": revoke_approval(u.path.split("/")[3])})
                return
            if u.path == "/api/workflows":
                self.send_json(201, {"workflow": save_workflow(self.read_json())})
                return
            if u.path.startswith("/api/workflows/") and u.path.endswith("/run"):
                data = self.read_json()
                result = asyncio.run(run_workflow_android(u.path.split("/")[3], str(data.get("input") or "")))
                self.send_json(200, {"run": result})
                return
            if u.path.startswith("/api/workflows/") and u.path.endswith("/validate"):
                workflow = get_workflow(u.path.split("/")[3])
                if not workflow:
                    raise ValueError("Workflow não encontrado.")
                self.send_json(200, {"errors": validate_workflow(workflow)})
                return
            if u.path == "/api/channels":
                self.send_json(201,{"channel":create_channel(self.read_json())}); return
            if u.path.startswith("/api/channels/") and u.path.endswith("/members"):
                cid=u.path.split("/")[3]; data=self.read_json(); members=set_channel_members(cid,list(data.get("members") or []))
                self.send_json(200,{"members":members,"channel":get_channel(cid)}); return
            if u.path.startswith("/api/channels/") and u.path.endswith("/messages"):
                cid=u.path.split("/")[3]; self.send_json(201,{"message":create_channel_message(cid,self.read_json())}); return
            if u.path.startswith("/api/channels/") and u.path.endswith("/invoke"):
                cid=u.path.split("/")[3]; data=self.read_json(); aid=str(data.get("agent_id") or "").strip(); content=str(data.get("content") or "").strip(); agent=get_agent(aid)
                if not agent or not content: raise ValueError("Agente e mensagem são obrigatórios.")
                create_channel_message(cid,{"content":content,"author_name":"Você"})
                result=asyncio.run(execute_agent_android(aid,content,source="direct"))
                reply=create_channel_message(cid,{"content":result.text,"author_type":"agent","author_id":aid,"author_name":str(agent.get("name") or aid),"kind":"message"})
                self.send_json(200,{"message":reply,"run_id":result.run_id,"model":result.model}); return
            if u.path == "/api/automations":
                self.send_json(201, {"automation": create_automation(self.read_json())})
                return
            if u.path == "/api/agents":
                self.send_json(201, {"agent": create_agent(self.read_json())})
                return
            if u.path.startswith("/api/agents/") and u.path.endswith("/logistics/erp"):
                aid=u.path.split("/")[3]; data=self.read_json(); action=str(data.get("action") or "").strip().lower()
                from meuharness.domains.logistics_erp import add_pending, board_snapshot, create_order, delete_order, set_pending, update_order
                if action=="create": result=create_order(aid,data)
                elif action=="update": result=update_order(aid,str(data.get("order_id") or ""),data)
                elif action=="delete": result=delete_order(aid,str(data.get("order_id") or ""))
                elif action=="add_pending": result=add_pending(aid,str(data.get("order_id") or ""),str(data.get("title") or ""),str(data.get("due_date") or ""))
                elif action=="set_pending": result=set_pending(aid,str(data.get("order_id") or ""),str(data.get("pending_id") or ""),str(data.get("status") or "done"))
                else: raise ValueError("Ação inválida para o ERP logístico")
                self.send_json(200,{"result":result,"board":board_snapshot(aid)}); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/crm"):
                aid=u.path.split("/")[3]; data=self.read_json(); contact=str(data.get("contact") or "").strip(); action=str(data.get("action") or "update").strip().lower()
                if not contact: raise ValueError("Contato é obrigatório.")
                from meuharness.domains.whatsapp_crm import add_item, update_crm
                if action in {"task","pending","order"}: result=add_item(aid,contact,action,data)
                elif action=="triage": self.send_json(501,{"error":"Triage WhatsApp exige o runtime desktop."}); return
                else: result=update_crm(aid,contact,data)
                self.send_json(200,result); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/whatsapp/sync"):
                self.send_json(501,{"error":"Sincronização WhatsApp exige o transport desktop."}); return
            if u.path.startswith("/api/agents/") and u.path.endswith("/browser/action"):
                self.send_json(501, {"error": "Harness Browser desktop não está disponível no Core Android embutido."})
                return
            if u.path == "/api/context-items":
                self.send_json(201, {"item": create_context_item(self.read_json())})
                return
            if u.path == "/api/projects":
                self.send_json(201, {"project": create_project(self.read_json())})
                return
            if u.path == "/api/conversations":
                data = self.read_json()
                self.send_json(201, {"conversation": create_conversation(str(data.get("agent_id") or "").strip(), data.get("title"))})
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
                    if isinstance(item, dict) and str(item.get("data_url") or "").startswith("data:image/"):
                        attachments.append({"name": str(item.get("name") or "imagem")[:120], "media_type": str(item.get("media_type") or "image/jpeg"), "data_url": str(item.get("data_url") or "")})
                prompt = str(data.get("content") or "").strip() or ("Analise a imagem anexada." if attachments else "")
                if not prompt:
                    raise ValueError("Mensagem vazia.")
                previous = [{"role": m.get("role"), "content": m.get("content")} for m in list(conversation.get("messages") or []) if m.get("role") in {"user", "assistant"} and m.get("status") != "error"][-16:]
                user_message = append_conversation_message(conversation_id, "user", prompt, attachments=attachments)
                try:
                    result = asyncio.run(execute_agent_android(agent_id, prompt, history=previous + [{"role": "user", "content": prompt}], source=str(data.get("source") or "user"), conversation_id=conversation_id, attachments=attachments))
                    assistant_message = append_conversation_message(conversation_id, "assistant", result.text, run_id=result.run_id)
                    self.send_json(200, {"text": result.text, "model": result.model, "elapsed_ms": result.elapsed_ms, "run_id": result.run_id, "user_message": user_message, "assistant_message": assistant_message, "conversation": get_conversation(conversation_id)})
                    return
                except Exception as exc:
                    append_conversation_message(conversation_id, "assistant", "Erro: " + str(exc), run_id=getattr(exc, "actis_run_id", None), status="error")
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
                        self.send_json(409, {"error": "Este agente já possui uma requisição em andamento."})
                        return
                    _GLOBAL_CHAT_BUSY_AGENTS.add(agent_id)
                try:
                    data = self.read_json()
                    history = data.get("history") or []
                    if not isinstance(history, list) or not history:
                        raise ValueError("Histórico vazio.")
                    prompt = str(history[-1].get("content") or "").strip()
                    result = asyncio.run(execute_agent_android(agent_id, prompt, history=history, source=str(data.get("source") or "user")))
                    self.send_json(200, {"text": result.text, "model": result.model, "elapsed_ms": result.elapsed_ms, "run_id": result.run_id})
                    return
                finally:
                    with _GLOBAL_CHAT_BUSY_LOCK:
                        _GLOBAL_CHAT_BUSY_AGENTS.discard(agent_id)
            self.send_json(404, {"error": "Endpoint não encontrado no Core Android."})
        except (HarnessError, ValueError, TypeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_PUT(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/channel-messages/"):
                mid=u.path.split("/")[3]; message=update_channel_message(mid,self.read_json())
                self.send_json(200 if message else 404,{"message":message} if message else {"error":"Mensagem não encontrada."}); return
            if u.path.startswith("/api/connectors/"):
                data=self.read_json(); data["id"]=u.path.split("/")[3]
                self.send_json(200,{"connector":_save_connector_local(data)}); return
            if u.path.startswith("/api/agent-context-bindings/"):
                data = self.read_json()
                self.send_json(200, {"bindings": set_agent_project_bindings(u.path.split("/")[3], list(data.get("project_ids") or []))})
                return
            if u.path.startswith("/api/workflows/"):
                self.send_json(200, {"workflow": save_workflow(self.read_json(), u.path.split("/")[3])})
                return
            if u.path.startswith("/api/automations/"):
                self.send_json(200, {"automation": update_automation(u.path.split("/")[3], self.read_json())})
                return
            if u.path.startswith("/api/agents/"):
                self.send_json(200, {"agent": update_agent(u.path.split("/")[3], self.read_json())})
                return
            if u.path.startswith("/api/companies/"):
                self.send_json(200, {"company": update_company(u.path.split("/")[3], self.read_json())})
                return
            if u.path.startswith("/api/sectors/"):
                self.send_json(200, {"sector": update_sector(u.path.split("/")[3], self.read_json())})
                return
            self.send_json(404, {"error": "Endpoint não encontrado no Core Android."})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path.startswith("/api/channel-messages/"):
                ok=delete_channel_message(u.path.split("/")[3]); self.send_json(200 if ok else 404,{"ok":bool(ok)}); return
            if u.path.startswith("/api/channels/"):
                ok=delete_channel(u.path.split("/")[3]); self.send_json(200 if ok else 400,{"ok":bool(ok)}); return
            if u.path.startswith("/api/connectors/"):
                cid=u.path.split("/")[3]; items=load_collection("connectors"); old=next((x for x in items if x.get("id")==cid),None)
                if not old or old.get("builtin"): self.send_json(400,{"ok":False,"error":"Connector protegido ou inexistente."}); return
                save_collection("connectors",[x for x in items if x.get("id")!=cid]); self.send_json(200,{"ok":True}); return
            if u.path.startswith("/api/context-items/"):
                ok = delete_context_item(u.path.split("/")[3])
                self.send_json(200 if ok else 404, {"ok": bool(ok)})
                return
            if u.path.startswith("/api/workflows/"):
                ok = delete_workflow(u.path.split("/")[3])
                self.send_json(200 if ok else 404, {"ok": bool(ok)})
                return
            if u.path.startswith("/api/automations/"):
                ok = delete_automation(u.path.split("/")[3])
                self.send_json(200 if ok else 404, {"ok": bool(ok)})
                return
            self.send_json(404, {"error": "Endpoint não encontrado no Core Android."})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    cancel_stale_running_runs()
    return ThreadingHTTPServer((host, port), AndroidHandler)
