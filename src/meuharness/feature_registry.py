"""Canonical ACTIS GEN feature catalog used by the General agent and documentation."""
from __future__ import annotations

from typing import Any


def _feature(
    feature_id: str,
    name: str,
    description: str,
    *,
    general_mode: str,
    general_actions: tuple[str, ...] = (),
    notes: str = "",
    status: str = "ready",
) -> dict[str, Any]:
    return {
        "id": feature_id,
        "name": name,
        "description": description,
        "status": status,
        "general_mode": general_mode,
        "general_actions": list(general_actions),
        "notes": notes,
    }


FEATURE_CATALOG: tuple[dict[str, Any], ...] = (
    _feature("agents", "Agentes", "Identidade, modelo, instruções, skills, tools e permissões.", general_mode="direct", general_actions=("list", "inspect", "create", "update", "transfer", "delete", "delegate")),
    _feature("organization", "Empresas e setores", "Organização visual e contextual, incluindo cor da empresa.", general_mode="direct", general_actions=("list", "create", "rename", "set_color", "move_agent")),
    _feature("company-runtime", "Company Runtime", "Comunicação intraempresa por capabilities e Company Tasks isoladas por empresa.", general_mode="direct", general_actions=("inspect", "configure_capabilities", "list_tasks")),
    _feature("context", "Projetos e contexto", "Contexto hierárquico empresa → setor → projeto → agente.", general_mode="direct", general_actions=("list", "create", "bind", "write", "delete")),
    _feature("memory", "Memória persistente", "Memória cross-conversation por agente, com FTS5 e fallback por recência.", general_mode="direct", general_actions=("recall", "inspect")),
    _feature("conversations", "Conversas", "Conversas persistentes separadas de Runs.", general_mode="direct", general_actions=("list", "inspect", "create")),
    _feature("runs-world", "World, Runs e tarefas", "Execução, atividade operacional, tarefas projetadas e eventos.", general_mode="direct", general_actions=("inspect", "cancel_run", "list_tasks", "list_events")),
    _feature("automations", "Automações", "Triggers interval, daily, once e condition.", general_mode="direct", general_actions=("list", "create", "update", "delete")),
    _feature("workflows", "Workflows", "Editor, validação, execução e histórico de workflows.", general_mode="direct", general_actions=("list", "inspect", "create", "update", "validate", "run", "delete")),
    _feature("approvals", "Approvals", "Concessão e revogação de scopes adicionais.", general_mode="direct", general_actions=("list", "approve", "deny", "revoke")),
    _feature("tools-skills", "Tools e Skill sets", "Catálogo de harnesses/tools e skills herdáveis com progressive disclosure.", general_mode="direct", general_actions=("discover", "assign_via_agent")),
    _feature("models", "Modelos e 9Router", "Catálogo e seleção de modelos/providers através do 9Router.", general_mode="direct", general_actions=("list_models", "assign_model")),
    _feature("connectors", "Connectors", "Registro de integrações, health check e acesso por agente.", general_mode="direct", general_actions=("list", "create", "update", "test", "delete")),
    _feature("channels", "Canais / Agent Bus", "Comunicação persistente e contexto de longo prazo entre agentes.", general_mode="direct", general_actions=("list", "create", "read", "send", "set_members", "update_message", "delete_message", "delete_channel")),
    _feature(
        "whatsapp",
        "WhatsApp local-first",
        "Shadow local, sincronização por contato, contexto persistente com evidências, inbox, handoff, atendimento reativo pelo kernel e outbox com destinatário e ACK verificados.",
        general_mode="hybrid",
        general_actions=("inspect_agent_state", "monitor_contact", "sync", "toggle_automation", "delegate_domain_action"),
        notes="O General descobre whatsapp_open, whatsapp_sync_contact, whatsapp_customer_context e whatsapp_save_context por delegação ao agente do domínio. inbound_jobs expõe cada atendimento recebido; whatsapp_automation_jobs inspeciona falhas/execução. O General administra e inspeciona o domínio; ações de atendimento/envio devem ser delegadas ao agente WhatsApp autorizado para preservar identidade e políticas do domínio.",
    ),
    _feature("harnesses", "Harnesses e toolkits", "Capabilities reutilizáveis publicadas dinamicamente no catálogo de tools do ACTIS.", general_mode="hybrid", general_actions=("discover", "assign_via_agent", "delegate"), notes="O General consulta o catálogo vivo de tools em vez de manter uma lista fixa; não precisa possuir todos os harnesses/toolkits e pode configurar ou delegar a agentes especializados."),
    _feature("desktop-assistant", "Desktop Assistant", "Assistente GTK experimental ligado a um agente desktop.operator.", general_mode="hybrid", general_actions=("discover_operator", "configure_agent", "delegate"), status="experimental"),
)


def list_feature_catalog() -> list[dict[str, Any]]:
    """Return a defensive copy of the current product feature catalog."""
    return [{**item, "general_actions": list(item.get("general_actions") or [])} for item in FEATURE_CATALOG]


def feature_ids() -> set[str]:
    return {str(item["id"]) for item in FEATURE_CATALOG}
