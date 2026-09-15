"""ACTIS GEN administrative tools exposed only to the General agent."""
from __future__ import annotations

import json
from typing import Annotated, Any

from meuharness.agents import (
    cancel_run_record,
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
from meuharness.approvals import list_approvals, resolve_approval, revoke_approval
from meuharness.automations import (
    create_automation,
    delete_automation,
    list_automations,
    update_automation,
)
from meuharness.contexts import (
    create_context_item,
    create_project,
    delete_context_item,
    list_agent_bindings,
    list_context_items,
    list_projects,
    set_agent_project_bindings,
)
from meuharness.event_bus import emit_event, list_events
from meuharness.observability import set_run_activity
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.tasks import list_tasks
from meuharness.tool_registry import list_tool_catalog
from meuharness.workflows import (
    delete_workflow,
    get_workflow,
    list_workflow_runs,
    list_workflows,
    run_workflow,
    save_workflow,
    validate_workflow,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} precisa ser JSON válido.") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} precisa ser um objeto JSON.")
    return value


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def general_state_snapshot() -> dict[str, Any]:
    """Compact live state used both by the General prompt and an explicit tool."""
    companies = list_companies()
    sectors = list_sectors()
    agents = list_agents()
    workflows = list_workflows()
    automations = list_automations()
    projects = list_projects()
    return {
        "agents": [
            {
                "id": a.get("id"), "name": a.get("name"), "model": a.get("model"),
                "company_id": a.get("company_id"), "sector_id": a.get("sector_id"),
                "tools": a.get("tools", []), "status": a.get("status"),
            }
            for a in agents
        ],
        "companies": companies,
        "sectors": sectors,
        "projects": projects,
        "workflows": [{"id": w.get("id"), "name": w.get("name"), "description": w.get("description")} for w in workflows],
        "automations": [
            {"id": a.get("id"), "name": a.get("name"), "agent_id": a.get("agent_id"),
             "trigger_type": a.get("trigger_type"), "enabled": a.get("enabled", True)}
            for a in automations
        ],
        "pending_approvals": list_approvals("pending"),
        "tools": list_tool_catalog(),
    }


def build_general_tools(env_file: str | None = None, *, run_id: str | None = None) -> list[object]:
    """Build General tools mirroring the ACTIS GEN UI/API control plane."""

    def actis_get_system_state() -> str:
        """Leia o estado vivo do ACTIS: agentes, empresas, setores, projetos, workflows, automações, approvals e tools."""
        return _dump(general_state_snapshot())

    def actis_list_agents() -> str:
        """Liste agentes existentes."""
        return _dump(list_agents())

    def actis_get_agent(agent_id: Annotated[str, "ID do agente"]) -> str:
        """Leia a configuração completa de um agente."""
        return _dump(get_agent(agent_id) or {"error": "agent_not_found"})

    def actis_create_agent(
        name: Annotated[str, "Nome visível"], model: Annotated[str, "ID exato do modelo"],
        instructions: Annotated[str, "Instruções permanentes"], icon: Annotated[str, "Ícone"] = "◆",
        tools_csv: Annotated[str, "Tools separadas por vírgula"] = "",
        permissions_csv: Annotated[str, "Scopes separados por vírgula"] = "",
        company_id: Annotated[str, "Empresa; vazio = sem empresa"] = "",
        sector_id: Annotated[str, "Setor; vazio = sem setor"] = "",
        memory: Annotated[bool, "Ativar memória"] = True,
    ) -> str:
        """Crie agente, inclusive já alocado em empresa/setor."""
        data: dict[str, Any] = {
            "name": name, "model": model, "instructions": instructions, "icon": icon,
            "tools": _csv(tools_csv), "memory": memory,
            "company_id": company_id or None, "sector_id": sector_id or None,
        }
        if permissions_csv.strip():
            data["permissions"] = _csv(permissions_csv)
        return _dump(create_agent(data))

    def actis_update_agent(
        agent_id: Annotated[str, "ID do agente"], name: Annotated[str | None, "Novo nome"] = None,
        model: Annotated[str | None, "Novo modelo"] = None,
        instructions: Annotated[str | None, "Novas instruções"] = None,
        icon: Annotated[str | None, "Novo ícone"] = None,
        tools_csv: Annotated[str | None, "Nova lista de tools"] = None,
        permissions_csv: Annotated[str | None, "Nova lista de scopes"] = None,
        company_id: Annotated[str | None, "Nova empresa; string vazia remove empresa"] = None,
        sector_id: Annotated[str | None, "Novo setor; string vazia remove setor"] = None,
        memory: Annotated[bool | None, "Novo estado da memória"] = None,
    ) -> str:
        """Edite ou transfira um agente entre empresa/setor."""
        data: dict[str, Any] = {}
        for key, value in (("name", name), ("model", model), ("instructions", instructions), ("icon", icon), ("memory", memory)):
            if value is not None:
                data[key] = value
        if tools_csv is not None: data["tools"] = _csv(tools_csv)
        if permissions_csv is not None: data["permissions"] = _csv(permissions_csv)
        if company_id is not None: data["company_id"] = company_id or None
        if sector_id is not None: data["sector_id"] = sector_id or None
        return _dump(update_agent(agent_id, data))

    def actis_list_companies() -> str:
        """Liste empresas e seus IDs."""
        return _dump(list_companies())

    def actis_create_company(name: Annotated[str, "Nome da empresa"]) -> str:
        """Crie uma empresa."""
        return _dump(create_company({"name": name}))

    def actis_update_company(company_id: Annotated[str, "ID da empresa"], name: Annotated[str, "Novo nome"]) -> str:
        """Renomeie uma empresa."""
        return _dump(update_company(company_id, {"name": name}))

    def actis_list_sectors(company_id: Annotated[str, "Filtrar por empresa; vazio = todos"] = "") -> str:
        """Liste setores."""
        return _dump(list_sectors(company_id or None))

    def actis_create_sector(name: Annotated[str, "Nome do setor"], company_id: Annotated[str, "ID da empresa"]) -> str:
        """Crie setor dentro de uma empresa."""
        return _dump(create_sector({"name": name, "company_id": company_id}))

    def actis_update_sector(sector_id: Annotated[str, "ID do setor"], name: Annotated[str, "Novo nome"]) -> str:
        """Renomeie um setor."""
        return _dump(update_sector(sector_id, {"name": name}))

    def actis_list_projects(company_id: Annotated[str, "Filtrar por empresa; vazio = todos"] = "") -> str:
        """Liste projetos de contexto."""
        return _dump(list_projects(company_id or None))

    def actis_create_project(name: Annotated[str, "Nome do projeto"], company_id: Annotated[str, "Empresa dona"], status: Annotated[str, "Status"] = "active") -> str:
        """Crie um projeto de contexto."""
        return _dump(create_project({"name": name, "company_id": company_id, "status": status}))

    def actis_list_context(scope_type: Annotated[str, "company, sector, project ou agent"] = "", scope_id: Annotated[str, "ID do escopo"] = "") -> str:
        """Liste conhecimentos/instruções de contexto hierárquico."""
        return _dump(list_context_items(scope_type or None, scope_id or None))

    def actis_create_context(
        scope_type: Annotated[str, "company, sector, project ou agent"], scope_id: Annotated[str, "ID do escopo"],
        title: Annotated[str, "Título"], content: Annotated[str, "Conteúdo"],
        kind: Annotated[str, "instruction, knowledge, document, fact, note, procedure, state ou reference"] = "knowledge",
        pinned: Annotated[bool, "Sempre carregar"] = False, priority: Annotated[int, "0 a 100"] = 50,
    ) -> str:
        """Adicione contexto persistente a empresa, setor, projeto ou agente."""
        return _dump(create_context_item({"scope_type": scope_type, "scope_id": scope_id, "title": title, "content": content, "kind": kind, "pinned": pinned, "priority": priority, "source": "general"}))

    def actis_delete_context(item_id: Annotated[str, "ID do item de contexto"]) -> str:
        """Remova um item de contexto."""
        return _dump({"ok": delete_context_item(item_id)})

    def actis_get_agent_project_bindings(agent_id: Annotated[str, "ID do agente"]) -> str:
        """Liste projetos ligados ao agente."""
        return _dump(list_agent_bindings(agent_id))

    def actis_set_agent_projects(agent_id: Annotated[str, "ID do agente"], project_ids_csv: Annotated[str, "IDs de projetos separados por vírgula; vazio remove todos"] = "") -> str:
        """Defina os projetos de contexto que um agente pode herdar."""
        return _dump(set_agent_project_bindings(agent_id, _csv(project_ids_csv)))

    async def actis_list_models() -> str:
        """Liste modelos anunciados pelo 9Router."""
        settings = NineRouterSettings.from_env(env_file)
        return _dump({"models": await NineRouterGateway(settings).list_models(), "selected": settings.model})

    def actis_list_runs(agent_id: Annotated[str, "Filtrar por agente; vazio = todos"] = "") -> str:
        """Liste runs recentes."""
        return _dump(list_runs(agent_id or None)[:50])

    def actis_cancel_run(run_id_to_cancel: Annotated[str, "ID da run"]) -> str:
        """Marque uma run persistida como cancelada. Use para run órfã; runs ativas são canceladas pelo kernel/UI."""
        return _dump({"ok": cancel_run_record(run_id_to_cancel, "Cancelada pelo General.")})

    def actis_list_tasks(active_only: Annotated[bool, "Somente ativas/aguardando/bloqueadas"] = False) -> str:
        """Liste tarefas operacionais projetadas no World."""
        return _dump(list_tasks(active_only=active_only, limit=200))

    def actis_list_events(event_type: Annotated[str, "Filtrar por tipo; vazio = todos"] = "", run_id_filter: Annotated[str, "Filtrar por run"] = "") -> str:
        """Leia eventos recentes do World/event bus."""
        return _dump(list_events(limit=200, event_type=event_type or None, run_id=run_id_filter or None))

    def actis_list_approvals(status: Annotated[str, "pending, approved, denied, revoked; vazio = todos"] = "") -> str:
        """Liste approvals de permissões."""
        return _dump(list_approvals(status or None))

    def actis_resolve_approval(approval_id: Annotated[str, "ID do approval"], approved: Annotated[bool, "True aprova, False nega"]) -> str:
        """Aprove ou negue uma solicitação de permissão."""
        return _dump(resolve_approval(approval_id, approved))

    def actis_revoke_approval(approval_id: Annotated[str, "ID de approval já aprovado"]) -> str:
        """Revogue uma permissão previamente aprovada."""
        return _dump(revoke_approval(approval_id))

    def actis_list_automations() -> str:
        """Liste automações."""
        return _dump(list_automations())

    def actis_create_automation(
        name: Annotated[str, "Nome"], agent_id: Annotated[str, "Agente executor"], action: Annotated[str, "Ação"],
        trigger_type: Annotated[str, "interval, daily, once ou condition"], interval_minutes: Annotated[int, "Intervalo em minutos"] = 60,
        daily_time: Annotated[str, "HH:MM"] = "09:00", once_at: Annotated[str, "ISO datetime"] = "",
        condition: Annotated[str, "Condição natural"] = "", check_every_minutes: Annotated[int, "Checagem da condição"] = 5,
    ) -> str:
        """Crie automação persistente."""
        data: dict[str, Any] = {"name": name, "agent_id": agent_id, "action": action, "trigger_type": trigger_type}
        if trigger_type == "interval": data["interval_seconds"] = max(1, interval_minutes) * 60
        elif trigger_type == "daily": data["daily_time"] = daily_time
        elif trigger_type == "once": data["once_at"] = once_at
        elif trigger_type == "condition": data.update({"condition": condition, "check_seconds": max(1, check_every_minutes) * 60})
        return _dump(create_automation(data))

    def actis_update_automation(automation_id: Annotated[str, "ID"], changes_json: Annotated[str, "Objeto JSON com campos a alterar"]) -> str:
        """Atualize qualquer campo suportado de uma automação."""
        return _dump(update_automation(automation_id, _json_object(changes_json, "changes_json")))

    def actis_delete_automation(automation_id: Annotated[str, "ID"]) -> str:
        """Exclua automação."""
        return _dump({"ok": delete_automation(automation_id)})

    def actis_list_workflows() -> str:
        """Liste workflows."""
        return _dump(list_workflows())

    def actis_get_workflow(workflow_id: Annotated[str, "ID do workflow"]) -> str:
        """Leia workflow completo com nós e conexões."""
        value = get_workflow(workflow_id)
        return _dump({"workflow": value, "validation": validate_workflow(value) if value else ["Workflow não encontrado."]})

    def actis_save_workflow(workflow_json: Annotated[str, "JSON com name, description, nodes e edges"], workflow_id: Annotated[str, "ID para editar; vazio cria novo"] = "") -> str:
        """Crie ou edite workflow exatamente como o editor visual."""
        data = _json_object(workflow_json, "workflow_json")
        workflow = save_workflow(data, workflow_id or None)
        return _dump({"workflow": workflow, "validation": validate_workflow(workflow)})

    def actis_delete_workflow(workflow_id: Annotated[str, "ID do workflow"]) -> str:
        """Exclua workflow."""
        return _dump({"ok": delete_workflow(workflow_id)})

    async def actis_run_workflow(workflow_id: Annotated[str, "ID do workflow"], workflow_input: Annotated[str, "Entrada do workflow"] = "") -> str:
        """Execute workflow e retorne trace/resultados."""
        return _dump(await run_workflow(workflow_id, workflow_input, env_file))

    def actis_list_workflow_runs(workflow_id: Annotated[str, "ID do workflow; vazio = todos"] = "") -> str:
        """Liste histórico de execuções de workflow."""
        return _dump(list_workflow_runs(workflow_id or None, limit=50))

    def actis_list_conversations(agent_id: Annotated[str, "Agente; vazio = todos"] = "") -> str:
        """Liste conversas persistentes."""
        return _dump(list_conversations(agent_id or None))

    def actis_get_conversation(conversation_id: Annotated[str, "ID da conversa"]) -> str:
        """Leia uma conversa persistente."""
        return _dump(get_conversation(conversation_id) or {"error": "conversation_not_found"})

    def actis_create_conversation(agent_id: Annotated[str, "ID do agente"], title: Annotated[str, "Título"] = "Nova conversa") -> str:
        """Crie uma janela de conversa persistente para um agente."""
        return _dump(create_conversation(agent_id, title))

    async def actis_run_agent(agent_id: Annotated[str, "Agente alvo"], prompt: Annotated[str, "Tarefa/mensagem"]) -> str:
        """Delegue uma tarefa para outro agente pelo kernel canônico."""
        if agent_id == "general": return _dump({"error": "cannot_delegate_to_self"})
        if not get_agent(agent_id): return _dump({"error": "agent_not_found"})
        from meuharness.execution_service import execute_agent
        if run_id:
            set_run_activity(run_id, "general", "delegating", detail=f"Delegando para {agent_id}")
            emit_event("delegation.started", run_id=run_id, agent_id="general", target_agent_id=agent_id, prompt=prompt[:300])
            set_run_activity(run_id, "general", "waiting_agent", detail=f"Aguardando {agent_id}")
        try:
            result = await execute_agent(agent_id, prompt, source="delegation", parent_agent_id="general", env_file=env_file)
        finally:
            if run_id: set_run_activity(run_id, "general", "thinking", detail="Processando retorno da delegação")
        if run_id: emit_event("delegation.completed", run_id=run_id, agent_id="general", target_agent_id=agent_id, child_run_id=result.run_id)
        return _dump({"run_id": result.run_id, "text": result.text, "model": result.model})

    return [
        actis_get_system_state,
        actis_list_agents, actis_get_agent, actis_create_agent, actis_update_agent,
        actis_list_companies, actis_create_company, actis_update_company,
        actis_list_sectors, actis_create_sector, actis_update_sector,
        actis_list_projects, actis_create_project,
        actis_list_context, actis_create_context, actis_delete_context,
        actis_get_agent_project_bindings, actis_set_agent_projects,
        actis_list_models, actis_list_runs, actis_cancel_run, actis_list_tasks, actis_list_events,
        actis_list_approvals, actis_resolve_approval, actis_revoke_approval,
        actis_list_automations, actis_create_automation, actis_update_automation, actis_delete_automation,
        actis_list_workflows, actis_get_workflow, actis_save_workflow, actis_delete_workflow,
        actis_run_workflow, actis_list_workflow_runs,
        actis_list_conversations, actis_get_conversation, actis_create_conversation,
        actis_run_agent,
    ]
