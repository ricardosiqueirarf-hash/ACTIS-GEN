"""ACTIS GEN administrative tools exposed only to the General agent."""
from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Any

from meuharness.artifacts import bubble_run_artifacts, list_run_attachments
from meuharness.agents import (
    cancel_run_record,
    create_agent,
    create_company,
    create_conversation,
    create_sector,
    delete_agent,
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
from meuharness.event_bus import emit_event, list_events
from meuharness.feature_registry import list_feature_catalog
from meuharness.memory import recall
from meuharness.observability import set_run_activity
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.skills import list_skill_catalog, runtime_features_for_skills
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
                "tools": a.get("tools", []), "skills": a.get("skills", []),
                "company_capabilities": a.get("company_capabilities", []),
                "runtime_features": sorted(runtime_features_for_skills(list(a.get("skills") or []))),
                "status": a.get("status"),
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
        "connectors": list_connectors(),
        "channels": list_channels(),
        "features": list_feature_catalog(),
        "tools": list_tool_catalog(),
        "skills": list_skill_catalog(),
    }


def build_general_tools(
    env_file: str | None = None, *, run_id: str | None = None, owner_agent_id: str = "actis-control"
) -> list[object]:
    """Build inheritable ACTIS control-plane tools for any authorized agent."""

    def actis_get_system_state() -> str:
        """Leia o estado vivo do ACTIS: agentes, empresas, setores, projetos, workflows, automações, approvals e tools."""
        return _dump(general_state_snapshot())

    def actis_list_features() -> str:
        """Liste as features atuais do produto e como o General deve acessá-las."""
        return _dump(list_feature_catalog())

    def actis_list_agents() -> str:
        """Liste o catálogo vivo de agentes existentes. Use novamente se agentes puderem ter sido criados/alterados durante a Run."""
        return _dump(list_agents())

    def actis_find_agents(query: Annotated[str, "Nome, ID, empresa, setor ou capability"]) -> str:
        """Busque agentes no catálogo vivo sem exigir que o usuário saiba o ID exato."""
        def key(value: object) -> str:
            raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
            return " ".join(raw.lower().split())

        q = key(query)
        rows = list_agents()
        if not q:
            return _dump(rows)
        companies = {str(c.get("id")): str(c.get("name") or "") for c in list_companies()}
        sectors = {str(row.get("id")): str(row.get("name") or "") for row in list_sectors()}
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for agent in rows:
            ident = key(agent.get("id"))
            name = key(agent.get("name"))
            haystack = key(" ".join([
                str(agent.get("id") or ""), str(agent.get("name") or ""), str(agent.get("model") or ""),
                companies.get(str(agent.get("company_id") or ""), ""), sectors.get(str(agent.get("sector_id") or ""), ""),
                *[str(x) for x in list(agent.get("skills") or [])], *[str(x) for x in list(agent.get("tools") or [])],
            ]))
            if q not in haystack:
                continue
            rank = 0 if q in {ident, name} else 1 if ident.startswith(q) or name.startswith(q) else 2
            ranked.append((rank, name, agent))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return _dump([agent for _, _, agent in ranked[:50]])

    def actis_list_skills() -> str:
        """Liste skills herdáveis disponíveis e suas tools/scopes resolvidas."""
        return _dump(list_skill_catalog())

    def actis_get_agent(agent_id: Annotated[str, "ID do agente"]) -> str:
        """Leia a configuração completa de um agente."""
        return _dump(get_agent(agent_id) or {"error": "agent_not_found"})

    def actis_create_agent(
        name: Annotated[str, "Nome visível"], model: Annotated[str, "ID exato do modelo"],
        instructions: Annotated[str, "Instruções permanentes"], icon: Annotated[str, "Ícone"] = "◆",
        tools_csv: Annotated[str, "Tools separadas por vírgula"] = "",
        skills_csv: Annotated[str, "Skills herdáveis separadas por vírgula"] = "",
        permissions_csv: Annotated[str, "Scopes separados por vírgula"] = "",
        company_capabilities_csv: Annotated[str, "Capabilities internas da empresa, separadas por vírgula"] = "",
        company_id: Annotated[str, "Empresa; vazio = sem empresa"] = "",
        sector_id: Annotated[str, "Setor; vazio = sem setor"] = "",
        memory: Annotated[bool, "Ativar memória"] = True,
    ) -> str:
        """Crie agente, inclusive já alocado em empresa/setor."""
        data: dict[str, Any] = {
            "name": name, "model": model, "instructions": instructions, "icon": icon,
            "tools": _csv(tools_csv), "skills": _csv(skills_csv), "memory": memory,
            "company_capabilities": _csv(company_capabilities_csv),
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
        skills_csv: Annotated[str | None, "Nova lista de skills herdáveis"] = None,
        permissions_csv: Annotated[str | None, "Nova lista de scopes"] = None,
        company_capabilities_csv: Annotated[str | None, "Nova lista de capabilities internas da empresa"] = None,
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
        if skills_csv is not None: data["skills"] = _csv(skills_csv)
        if permissions_csv is not None: data["permissions"] = _csv(permissions_csv)
        if company_capabilities_csv is not None: data["company_capabilities"] = _csv(company_capabilities_csv)
        if company_id is not None: data["company_id"] = company_id or None
        if sector_id is not None: data["sector_id"] = sector_id or None
        return _dump(update_agent(agent_id, data))

    def actis_delete_agent(agent_id: Annotated[str, "ID do agente a excluir"]) -> str:
        """Exclua um agente não sistêmico após verificar dependências que ficariam quebradas."""
        target = get_agent(agent_id)
        if not target:
            return _dump({"ok": False, "error": "agent_not_found", "agent_id": agent_id})
        if agent_id == "general":
            return _dump({"ok": False, "error": "protected_system_agent", "agent_id": agent_id})
        blockers: dict[str, Any] = {}
        active_runs = [run.get("id") for run in list_runs(agent_id) if run.get("status") == "running"]
        if active_runs:
            blockers["running_runs"] = active_runs
        automation_refs = [a.get("id") for a in list_automations() if a.get("agent_id") == agent_id]
        if automation_refs:
            blockers["automations"] = automation_refs
        workflow_refs = []
        for workflow in list_workflows():
            nodes = [node.get("id") for node in list(workflow.get("nodes") or []) if node.get("agent_id") == agent_id]
            if nodes:
                workflow_refs.append({"workflow_id": workflow.get("id"), "node_ids": nodes})
        if workflow_refs:
            blockers["workflows"] = workflow_refs
        context_refs = [item.get("id") for item in list_context_items("agent", agent_id)]
        if context_refs:
            blockers["context_items"] = context_refs
        if blockers:
            return _dump({"ok": False, "error": "agent_in_use", "agent_id": agent_id, "blockers": blockers})

        set_agent_project_bindings(agent_id, [])
        for channel in list_channels():
            full = get_channel(str(channel.get("id") or "")) or {}
            members = [str(item) for item in list(full.get("members") or [])]
            if agent_id in members:
                set_channel_members(str(channel["id"]), [item for item in members if item != agent_id])
        deleted = delete_agent(agent_id)
        return _dump({
            "ok": deleted and get_agent(agent_id) is None,
            "deleted": deleted,
            "agent_id": agent_id,
            "verified_absent": get_agent(agent_id) is None,
            "history_preserved": True,
        })

    def actis_list_companies() -> str:
        """Liste empresas e seus IDs."""
        return _dump(list_companies())

    def actis_create_company(
        name: Annotated[str, "Nome da empresa"],
        color: Annotated[str, "Cor #RRGGBB; vazio usa paleta automática"] = "",
    ) -> str:
        """Crie uma empresa, opcionalmente já definindo sua cor visual."""
        data: dict[str, Any] = {"name": name}
        if color.strip():
            data["color"] = color.strip()
        return _dump(create_company(data))

    def actis_update_company(
        company_id: Annotated[str, "ID da empresa"],
        name: Annotated[str, "Novo nome; vazio preserva"] = "",
        color: Annotated[str, "Nova cor #RRGGBB; vazio preserva"] = "",
    ) -> str:
        """Atualize nome e/ou cor visual de uma empresa."""
        data: dict[str, Any] = {}
        if name.strip():
            data["name"] = name.strip()
        if color.strip():
            data["color"] = color.strip()
        return _dump(update_company(company_id, data))

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
        return _dump(create_context_item({"scope_type": scope_type, "scope_id": scope_id, "title": title, "content": content, "kind": kind, "pinned": pinned, "priority": priority, "source": owner_agent_id}))

    def actis_delete_context(item_id: Annotated[str, "ID do item de contexto"]) -> str:
        """Remova um item de contexto."""
        return _dump({"ok": delete_context_item(item_id)})

    def actis_get_agent_project_bindings(agent_id: Annotated[str, "ID do agente"]) -> str:
        """Liste projetos ligados ao agente."""
        return _dump(list_agent_bindings(agent_id))

    def actis_recall_agent_memory(
        agent_id: Annotated[str, "ID do agente"],
        query: Annotated[str, "Consulta de memória"],
        limit: Annotated[int, "Máximo de itens"] = 10,
    ) -> str:
        """Consulte a memória persistente de um agente sem alterá-la."""
        if not get_agent(agent_id):
            return _dump({"error": "agent_not_found"})
        return _dump(recall(agent_id, query, limit=max(1, min(limit, 20))))

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

    def actis_get_run_checkpoints(run_id_to_read: Annotated[str, "ID da run"]) -> str:
        """Leia checkpoints/evidências duráveis de uma Run."""
        return _dump(list_checkpoints(run_id_to_read))

    async def actis_resume_run(run_id_to_resume: Annotated[str, "ID da run interrompida/planejada"]) -> str:
        """Retome uma Run pelo journal durável, criando nova tentativa ligada à anterior."""
        from meuharness.execution_service import resume_run
        result = await resume_run(run_id_to_resume, env_file=env_file)
        return _dump({"run_id": result.run_id, "resumed_from": run_id_to_resume, "text": result.text, "model": result.model})

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

    def actis_list_connectors() -> str:
        """Liste connectors ativos/configurados e quais agentes podem usá-los."""
        return _dump(list_connectors())

    def actis_save_connector(
        connector_id: Annotated[str, "ID; vazio cria novo"] = "",
        name: Annotated[str, "Nome visível"] = "",
        kind: Annotated[str, "nine_router, agent_bus ou http; vazio preserva"] = "",
        base_url: Annotated[str, "URL/base do serviço; vazio preserva"] = "",
        auth_type: Annotated[str, "none, bearer_env ou api_key_env; vazio preserva"] = "",
        credential_ref: Annotated[str, "Variável de ambiente; vazio preserva"] = "",
        enabled: Annotated[bool, "Connector habilitado"] = True,
        agent_ids_csv: Annotated[str, "Agentes permitidos; vazio preserva, * = todos"] = "",
        capabilities_csv: Annotated[str, "Capabilities; vazio preserva"] = "",
        operations_json: Annotated[str, "JSON array de operations HTTP executáveis; vazio preserva"] = "",
    ) -> str:
        """Crie ou atualize connector sem apagar campos omitidos em registros existentes."""
        clean_id = connector_id.strip()
        current = next((item for item in list_connectors() if item.get("id") == clean_id), None)
        data: dict[str, Any] = {
            "name": name or (current or {}).get("name") or clean_id or "Connector",
            "kind": kind or (current or {}).get("kind") or "http",
            "base_url": base_url or (current or {}).get("base_url") or "",
            "auth_type": auth_type or (current or {}).get("auth_type") or "none",
            "credential_ref": credential_ref or (current or {}).get("credential_ref") or "",
            "enabled": enabled,
            "agent_ids": _csv(agent_ids_csv) if agent_ids_csv.strip() else list((current or {}).get("agent_ids") or ["*"]),
            "capabilities": _csv(capabilities_csv) if capabilities_csv.strip() else list((current or {}).get("capabilities") or []),
            "operations": json.loads(operations_json) if operations_json.strip() else list((current or {}).get("operations") or []),
        }
        if not isinstance(data["operations"], list):
            raise ValueError("operations_json precisa ser um array JSON.")
        if clean_id:
            data["id"] = clean_id
        return _dump(save_connector(data))

    def actis_test_connector(connector_id: Annotated[str, "ID do connector"]) -> str:
        """Execute o health check real do connector."""
        return _dump(test_connector(connector_id, env_file))

    def actis_delete_connector(connector_id: Annotated[str, "ID do connector"]) -> str:
        """Exclua connector customizado; connectors built-in são protegidos."""
        return _dump({"ok": delete_connector(connector_id)})

    def actis_list_channels() -> str:
        """Liste canais persistentes do ACTIS Agent Bus."""
        return _dump(list_channels())

    def actis_create_channel(
        name: Annotated[str, "Nome do canal"],
        topic: Annotated[str, "Tópico/objetivo"] = "",
        scope_type: Annotated[str, "global, company, sector, agent ou members"] = "global",
        scope_id: Annotated[str, "ID do escopo quando aplicável"] = "",
        members_csv: Annotated[str, "IDs de agentes membros"] = "",
    ) -> str:
        """Crie um canal persistente do Agent Bus."""
        return _dump(create_channel({
            "name": name, "topic": topic, "scope_type": scope_type,
            "scope_id": scope_id or None, "members": _csv(members_csv),
        }))

    def actis_set_channel_members(
        channel_id: Annotated[str, "ID do canal"],
        members_csv: Annotated[str, "IDs de agentes; vazio remove todos"] = "",
    ) -> str:
        """Defina explicitamente os membros de um canal."""
        return _dump(set_channel_members(channel_id, _csv(members_csv)))

    def actis_read_channel(channel_id: Annotated[str, "ID do canal"], limit: Annotated[int, "Quantidade máxima"] = 30) -> str:
        """Leia mensagens recentes de um canal do ACTIS Agent Bus."""
        channel = get_channel(channel_id)
        if not channel:
            return _dump({"error": "channel_not_found"})
        return _dump({"channel": channel, "messages": list_channel_messages(channel_id, limit=max(1, min(limit, 100)))})

    def actis_send_channel_message(
        channel_id: Annotated[str, "ID do canal"],
        content: Annotated[str, "Mensagem a publicar"],
        kind: Annotated[str, "message, decision, goal, rule, note"] = "message",
    ) -> str:
        """Publique uma mensagem persistente no ACTIS Agent Bus em nome do agente atual."""
        if not get_channel(channel_id):
            return _dump({"error": "channel_not_found"})
        author = get_agent(owner_agent_id) or {}
        return _dump(create_channel_message(channel_id, {
            "content": content, "kind": kind, "author_type": "agent",
            "author_id": owner_agent_id, "author_name": str(author.get("name") or owner_agent_id),
        }))

    def actis_update_channel_message(
        message_id: Annotated[str, "ID da mensagem"],
        pinned: Annotated[bool | None, "Fixar/desfixar"] = None,
        long_term: Annotated[bool | None, "Entrar/sair do contexto de longo prazo"] = None,
        status: Annotated[str, "active, done ou archived; vazio preserva"] = "",
    ) -> str:
        """Atualize estado persistente de uma mensagem do Agent Bus."""
        data: dict[str, Any] = {}
        if pinned is not None:
            data["pinned"] = pinned
        if long_term is not None:
            data["long_term"] = long_term
        if status.strip():
            data["status"] = status.strip()
        return _dump(update_channel_message(message_id, data) or {"error": "message_not_found_or_no_changes"})

    def actis_delete_channel_message(message_id: Annotated[str, "ID da mensagem"]) -> str:
        """Apague uma mensagem de canal."""
        return _dump({"ok": delete_channel_message(message_id)})

    def actis_delete_channel(channel_id: Annotated[str, "ID do canal"]) -> str:
        """Apague um canal não protegido. #general não pode ser apagado."""
        return _dump({"ok": delete_channel(channel_id)})

    def actis_get_whatsapp_state(agent_id: Annotated[str, "Agente WhatsApp alvo"]) -> str:
        """Leia o estado local-first de um agente WhatsApp sem enviar mensagens."""
        agent = get_agent(agent_id)
        if not agent:
            return _dump({"error": "agent_not_found"})
        if "whatsapp.local" not in runtime_features_for_skills(list(agent.get("skills") or [])):
            return _dump({"error": "whatsapp_not_enabled", "agent_id": agent_id})
        from meuharness.domains.whatsapp_shadow import (
            list_inbox,
            list_monitored_contacts,
            pending_outbox,
            runtime_settings,
            sync_state_for_contact,
        )
        from meuharness.domains.whatsapp_automation import list_jobs
        monitored = list_monitored_contacts(agent_id)
        return _dump({
            "agent_id": agent_id,
            "runtime": runtime_settings(agent_id),
            "monitored": monitored,
            "continuity": {row["name"]: sync_state_for_contact(agent_id, row["name"]) for row in monitored},
            "inbox": list_inbox(agent_id, limit=80),
            "outbox": pending_outbox(agent_id, limit=40),
            "inbound_jobs": list_jobs(agent_id),
        })

    def actis_whatsapp_monitor_contact(
        agent_id: Annotated[str, "Agente WhatsApp alvo"],
        contact: Annotated[str, "Contato ou grupo"],
        phone: Annotated[str, "Telefone/ID externo opcional"] = "",
        enabled: Annotated[bool, "Monitoramento ativo"] = True,
        sync_history: Annotated[bool, "Solicitar backfill de histórico"] = False,
    ) -> str:
        """Configure monitoramento local de contato/grupo para um agente WhatsApp."""
        agent = get_agent(agent_id)
        if not agent or "whatsapp.local" not in runtime_features_for_skills(list(agent.get("skills") or [])):
            return _dump({"error": "whatsapp_agent_not_found", "agent_id": agent_id})
        from meuharness.domains.whatsapp_shadow import upsert_contact
        return _dump(upsert_contact(agent_id, contact, external_id=phone or None, monitored=enabled, sync_history=sync_history))

    def actis_whatsapp_sync(agent_id: Annotated[str, "Agente WhatsApp alvo"]) -> str:
        """Execute uma sincronização do shadow local do agente WhatsApp."""
        agent = get_agent(agent_id)
        if not agent or "whatsapp.local" not in runtime_features_for_skills(list(agent.get("skills") or [])):
            return _dump({"error": "whatsapp_agent_not_found", "agent_id": agent_id})
        from meuharness.domains.whatsapp_sync import sync_once
        return _dump(sync_once(agent_id))

    def actis_whatsapp_set_automation(agent_id: Annotated[str, "Agente WhatsApp alvo"], enabled: Annotated[bool, "Kill switch global do agente"]) -> str:
        """Ligue/desligue a automação WhatsApp de um agente; não envia mensagens."""
        agent = get_agent(agent_id)
        if not agent or "whatsapp.local" not in runtime_features_for_skills(list(agent.get("skills") or [])):
            return _dump({"error": "whatsapp_agent_not_found", "agent_id": agent_id})
        from meuharness.domains.whatsapp_shadow import set_automation_enabled
        return _dump(set_automation_enabled(agent_id, enabled))

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
        if agent_id == owner_agent_id: return _dump({"error": "cannot_delegate_to_self"})
        if not get_agent(agent_id): return _dump({"error": "agent_not_found"})
        from meuharness.execution_service import execute_agent
        if run_id:
            set_run_activity(run_id, owner_agent_id, "delegating", detail=f"Delegando para {agent_id}")
            emit_event("delegation.started", run_id=run_id, agent_id=owner_agent_id, target_agent_id=agent_id, prompt=prompt[:300])
            set_run_activity(run_id, owner_agent_id, "waiting_agent", detail=f"Aguardando {agent_id}")
        try:
            result = await execute_agent(agent_id, prompt, source="delegation", parent_agent_id=owner_agent_id, env_file=env_file)
        finally:
            if run_id: set_run_activity(run_id, owner_agent_id, "thinking", detail="Processando retorno da delegação")
        child_artifacts = list_run_attachments(result.run_id)
        if run_id and child_artifacts:
            bubble_run_artifacts(result.run_id, run_id, owner_agent_id)
        if run_id: emit_event("delegation.completed", run_id=run_id, agent_id=owner_agent_id, target_agent_id=agent_id, child_run_id=result.run_id)
        return _dump({"run_id": result.run_id, "text": result.text, "model": result.model, "artifacts": child_artifacts})

    return [
        actis_get_system_state, actis_list_features,
        actis_list_agents, actis_find_agents, actis_list_skills, actis_get_agent, actis_create_agent, actis_update_agent, actis_delete_agent,
        actis_list_companies, actis_create_company, actis_update_company,
        actis_list_sectors, actis_create_sector, actis_update_sector,
        actis_list_projects, actis_create_project,
        actis_list_context, actis_create_context, actis_delete_context,
        actis_get_agent_project_bindings, actis_recall_agent_memory, actis_set_agent_projects,
        actis_list_models, actis_list_runs, actis_get_run_checkpoints, actis_resume_run, actis_cancel_run, actis_list_tasks, actis_list_events,
        actis_list_approvals, actis_resolve_approval, actis_revoke_approval,
        actis_list_automations, actis_create_automation, actis_update_automation, actis_delete_automation,
        actis_list_workflows, actis_get_workflow, actis_save_workflow, actis_delete_workflow,
        actis_run_workflow, actis_list_workflow_runs,
        actis_list_connectors, actis_save_connector, actis_test_connector, actis_delete_connector,
        actis_list_channels, actis_create_channel, actis_set_channel_members,
        actis_read_channel, actis_send_channel_message, actis_update_channel_message,
        actis_delete_channel_message, actis_delete_channel,
        actis_get_whatsapp_state, actis_whatsapp_monitor_contact,
        actis_whatsapp_sync, actis_whatsapp_set_automation,
        actis_list_conversations, actis_get_conversation, actis_create_conversation,
        actis_run_agent,
    ]
