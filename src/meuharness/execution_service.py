"""Single execution kernel for every ACTIS GEN agent invocation."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from meuharness import HarnessRequest
from meuharness.adapters.maf.browser_tools import build_harness_browser
from meuharness.adapters.maf.observed_tool import (
    clear_run_tool_trace,
    get_run_tool_trace,
    observe_native_tool,
)
from meuharness.adapters.maf.system_tools import (
    build_computer_observer,
    build_harness_computer,
    build_harness_files,
    build_harness_terminal,
)
from meuharness.agents import cancel_run_record, finish_run, get_agent, get_run, start_run
from meuharness.approvals import (
    TOOL_SCOPES,
    approved_scopes,
    request_approval,
    scopes_for_tool,
    tool_for_scope,
)
from meuharness.artifacts import bubble_run_artifacts, list_run_attachments
from meuharness.bootstrap import create_harness
from meuharness.channels import channel_context_for_agent, channel_context_instructions
from meuharness.chat_attachments import pdf_attachment_context
from meuharness.connector_runtime import build_connector_tools, connector_runtime_catalog
from meuharness.contexts import context_instructions, resolve_agent_context
from meuharness.durable_runs import append_checkpoint, list_checkpoints, recovery_summary
from meuharness.event_bus import emit_event
from meuharness.memory import recall, remember
from meuharness.observability import set_run_activity
from meuharness.pdf_tools import build_pdf_tools
from meuharness.project_memory import project_memory_for_agent, project_memory_instructions
from meuharness.providers.nine_router import NineRouterSettings
from meuharness.public_tools import build_public_tools
from meuharness.skills import (
    progressive_skill_context,
    runtime_features_for_skills,
    scopes_for_skills,
    tools_for_skills,
)
from meuharness.spreadsheet_tools import build_excel_tools
from meuharness.tasks import create_task_for_run, update_task_by_run, update_work_by_run
from meuharness.tool_registry import (
    inheritable_tool_ids,
    list_tool_catalog,
    normalize_scopes,
    normalize_tool_ids,
    scopes_for_tools,
    skill_instructions_for_tools,
)

VALID_SOURCES = {"user", "direct", "delegation", "automation", "automation_condition", "resume"}
AFFIRMATIVE = {"sim", "pode", "pode sim", "autorizo", "autorizado", "ok", "claro"}

_ACTIVE_LOCK = threading.RLock()
_ACTIVE_EXECUTIONS: dict[str, dict[str, Any]] = {}

_BROWSER_MUTATION_TOOLS = {
    "browser_click", "browser_type", "browser_fill", "browser_press", "browser_upload_file",
}
_SELF_VERIFYING_BROWSER_ACTION_TOOLS = {"colorglass_quote_create", "colorglass_quote_add_door", "colorglass_quote_create_with_door", "colorglass_quote_add_door_fast"}
_BROWSER_ACTION_TOOLS = _BROWSER_MUTATION_TOOLS | {"browser_goto"} | _SELF_VERIFYING_BROWSER_ACTION_TOOLS
_BROWSER_VERIFICATION_TOOLS = {"browser_js", "browser_page_info", "colorglass_quote_verify"}
_BROWSER_BACKED_TOOLS = _BROWSER_ACTION_TOOLS | _BROWSER_VERIFICATION_TOOLS
_BROWSER_SUCCESS_RE = re.compile(
    r"\b(enviad[oa]s?|enviei|publicad[oa]s?|salv[oa]|salvei|apag(?:ad[oa]|uei)|"
    r"exclu[ií]d[oa]s?|criad[oa]s?|alterad[oa]s?|adicionad[oa]s?|removid[oa]s?|"
    r"conclu[ií]d[oa]s?|feito|sent|submitted|saved|deleted|posted|created|updated)\b",
    re.IGNORECASE,
)
_UNFINISHED_INTENT_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|certo|claro|entendi|perfeito|beleza)[!,. :;\s-]*)?(?:agora\s+)?(?:vou|irei|vamos|deixa\s+eu|deixe-me|"
    r"i(?:'ll|\s+will)|let\s+me)\s+(?:verificar|checar|conferir|analisar|abrir|acessar|"
    r"buscar|procurar|atualizar|alterar|corrigir|criar|executar|testar|investigar|fazer|"
    r"check|verify|inspect|open|search|update|change|fix|create|run|test|investigate|do)\b",
    re.IGNORECASE,
)
_OUTCOME_RE = re.compile(
    r"\b(?:resultado|resultados|encontrei|achei|verifiquei|confirmei|identifiquei|"
    r"atualizei|alterei|corrigi|criei|executei|testei|conclu[ií]|feito|pronto|"
    r"found|verified|confirmed|identified|updated|changed|fixed|created|executed|tested|done)\b",
    re.IGNORECASE,
)
_ACTION_REQUEST_RE = re.compile(
    r"\b(?:abra|abrir|acesse|acessar|altere|alterar|atualize|atualizar|corrija|corrigir|"
    r"crie|criar|delete|deletar|remova|remover|envie|enviar|mande|mandar|responda|responder|"
    r"busque|buscar|procure|procurar|verifique|verificar|cheque|checar|confira|conferir|"
    r"execute|executar|rode|rodar|teste|testar|instale|instalar|edite|editar|salve|salvar|"
    r"publique|publicar|faça|faca|fazer|implemente|implementar|analise|analisar|"
    r"open|access|change|update|fix|create|delete|remove|send|reply|search|find|verify|check|"
    r"run|test|install|edit|save|publish|implement|analyze)\b",
    re.IGNORECASE,
)

_COLORGLASS_QUOTE_ACTION_RE = re.compile(
    r"\b(?:or[çc]e|or[çc]ar|or[çc]amento|cot(?:e|ar|a[çc][aã]o)|crie\s+(?:um\s+)?or[çc]amento|"
    r"fa[çc]a\s+(?:um\s+)?or[çc]amento|monte\s+(?:um\s+)?or[çc]amento)\b",
    re.IGNORECASE,
)
_COLORGLASS_DOMAIN_RE = re.compile(
    r"\b(?:porta(?:s)?|perfil|vidro|espelho|dobradi[çc]a|puxador|colorglass|color\s*glass)\b",
    re.IGNORECASE,
)
_COLORGLASS_META_RE = re.compile(
    r"\b(?:lat[eê]ncia|arquitetura|agente|fluxo|como\s+funciona|status|progresso|teste\s+do\s+agente)\b",
    re.IGNORECASE,
)


def _fast_colorglass_quote_route(agent_id: str, prompt: str, source: str, attachments: list[dict[str, Any]] | None) -> bool:
    if agent_id != "general" or source not in {"user", "direct"}:
        return False
    text = str(prompt or "")
    quote_action = bool(_COLORGLASS_QUOTE_ACTION_RE.search(text) and _COLORGLASS_DOMAIN_RE.search(text))
    existing_add = bool(
        re.search(r"\b(?:adicione|adicionar|inclua|incluir|acrescente|acrescentar)\b", text, re.IGNORECASE)
        and re.search(r"\bporta(?:s)?\b", text, re.IGNORECASE)
        and re.search(r"\b(?:pedido|or[çc]amento)\s*#?\s*\d+\b", text, re.IGNORECASE)
    )
    if not (quote_action or existing_add):
        return False
    # Pedidos sobre o funcionamento/latência do agente continuam no General.
    if _COLORGLASS_META_RE.search(text) and not re.search(r"\b(?:crie|fa[çc]a|or[çc]e|or[çc]ar|monte)\b", text, re.IGNORECASE):
        return False
    return True


def _fast_quote_tool_choice(agent_id: str, prompt: str) -> dict[str, str] | None:
    if agent_id != "or-amentos-colorglass":
        return None
    text = str(prompt or "")
    quote_action = bool(_COLORGLASS_QUOTE_ACTION_RE.search(text) and _COLORGLASS_DOMAIN_RE.search(text))
    alteration_mode = bool(
        re.search(r'"quote_mode"\s*:\s*"alteration"', text, re.IGNORECASE)
        or re.search(r"\bALTERA[ÇC][AÃ]O DE OR[ÇC]AMENTO EXISTENTE\b", text, re.IGNORECASE)
    )
    existing = re.search(r"\b(?:pedido|or[çc]amento)\s*#?\s*\d+\b", text, re.IGNORECASE)
    if alteration_mode:
        return None
    add_existing = bool(existing and re.search(r"\b(?:adicione|adicionar|inclua|incluir|acrescente|acrescentar)\b", text, re.IGNORECASE))
    if not (quote_action or add_existing):
        return None
    if add_existing:
        return {"mode": "required", "required_function_name": "colorglass_quote_add_door_fast"}
    if re.search(r"\b(?:crie|fa[çc]a|monte|gere|novo|nova|or[çc]e|or[çc]ar)\b", text, re.IGNORECASE):
        return {"mode": "required", "required_function_name": "colorglass_quote_create_with_door"}
    return None

_ACK_ONLY_RE = re.compile(
    r"^\s*(?:ok(?:ay)?|certo|beleza|entendi|feito|pronto|sim|claro|combinado|"
    r"perfeito|ótimo|otimo|conclu[ií]do|done|sure|got it|understood|all set)[.!…\s]*$",
    re.IGNORECASE,
)
_FUTURE_ACTION_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|certo|claro|entendi|perfeito|beleza)[!,. :;\s-]*)?(?:agora\s+)?(?:vou|irei|vamos|deixa\s+eu|deixe-me|"
    r"i(?:'ll|\s+will)|let\s+me)\b",
    re.IGNORECASE,
)


def _prompt_requires_action(prompt: str) -> bool:
    return bool(_ACTION_REQUEST_RE.search(str(prompt or "")))


def _successful_tool_names(run_id: str) -> set[str]:
    names = {
        str(item.get("tool") or "")
        for item in get_run_tool_trace(run_id)
        if item.get("ok") and item.get("tool")
    }
    for checkpoint in list_checkpoints(run_id, limit=1000):
        if checkpoint.get("phase") == "tool" and checkpoint.get("status") == "completed":
            name = str(checkpoint.get("detail") or "").strip()
            if name:
                names.add(name)
    return names


def _has_browser_evidence(run_id: str) -> bool:
    return any(
        name.startswith("browser_") or name in _BROWSER_BACKED_TOOLS
        for name in _successful_tool_names(run_id)
    )


def _response_is_unfinished_intent(
    run_id: str, text: str, *, prompt: str = "", require_browser_tool: bool = False,
    require_action_tool: bool = False,
) -> bool:
    compact = " ".join(str(text or "").split()).strip()
    trace = get_run_tool_trace(run_id)
    if require_browser_tool and _prompt_requires_action(prompt):
        if not _has_browser_evidence(run_id):
            return True
    has_tool_evidence = any(item.get("ok") for item in trace)
    successful_tools = _successful_tool_names(run_id)
    if (
        re.search(r"\b(?:use|via|usando)\s+company_request_task\b", str(prompt or ""), re.IGNORECASE)
        and "company_request_task" not in successful_tools
    ):
        return True
    if require_action_tool and _prompt_requires_action(prompt) and not has_tool_evidence:
        return True
    if not compact or len(compact) > 700:
        return False
    if has_tool_evidence:
        return False
    if _prompt_requires_action(prompt) and _ACK_ONLY_RE.fullmatch(compact):
        return True
    if _FUTURE_ACTION_RE.search(compact) and not _OUTCOME_RE.search(compact):
        return True
    return bool(_UNFINISHED_INTENT_RE.search(compact) and not _OUTCOME_RE.search(compact))


def _tool_evidence(run_id: str) -> list[str]:
    return [
        f"{item.get('tool')}: {'concluída' if item.get('ok') else 'falhou'}"
        for item in get_run_tool_trace(run_id)[-6:]
        if item.get("tool")
    ]


def _browser_success_is_verified(run_id: str, text: str) -> bool:
    if not _BROWSER_SUCCESS_RE.search(str(text or "")):
        return True
    names = _successful_tool_names(run_id)
    if names & _SELF_VERIFYING_BROWSER_ACTION_TOOLS:
        return True
    trace = [item for item in get_run_tool_trace(run_id) if item.get("ok")]
    last_mutation = max(
        (index for index, item in enumerate(trace) if item.get("tool") in _BROWSER_MUTATION_TOOLS),
        default=-1,
    )
    if last_mutation < 0:
        return True
    return any(
        item.get("tool") in _BROWSER_VERIFICATION_TOOLS
        for item in trace[last_mutation + 1 :]
    )


def _required_browser_action_missing(run_id: str, prompt: str, runtime_features: set[str]) -> bool:
    if "browser.required_for_action" not in runtime_features or not _prompt_requires_action(prompt):
        return False
    return not bool(_successful_tool_names(run_id) & _BROWSER_ACTION_TOOLS)


def _task_finished_at() -> str:
    return datetime.now(UTC).isoformat()


def _register_execution(run_id: str, agent_id: str, *, computer: bool) -> None:
    task = asyncio.current_task()
    if task is None:
        return
    with _ACTIVE_LOCK:
        _ACTIVE_EXECUTIONS[run_id] = {
            "loop": asyncio.get_running_loop(),
            "task": task,
            "agent_id": agent_id,
            "computer": computer,
        }


def _unregister_execution(run_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_EXECUTIONS.pop(run_id, None)


def cancel_active_run(run_id: str) -> bool:
    with _ACTIVE_LOCK:
        execution = _ACTIVE_EXECUTIONS.get(run_id)
    if not execution:
        return False
    task = execution["task"]
    loop = execution["loop"]
    if task.done():
        return False
    loop.call_soon_threadsafe(task.cancel)
    return True


def active_computer_run_ids() -> list[str]:
    with _ACTIVE_LOCK:
        return [
            run_id for run_id, item in _ACTIVE_EXECUTIONS.items()
            if item.get("computer") and not item["task"].done()
        ]


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    text: str
    model: str
    elapsed_ms: float | None
    run_id: str
    effective_tools: tuple[str, ...]


def _normalized_history(history: list[dict[str, Any]] | None, prompt: str) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    for item in history or []:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            clean.append({"role": role, "content": content})
    if not clean or clean[-1]["content"] != prompt or clean[-1]["role"] != "user":
        clean.append({"role": "user", "content": prompt})
    return clean[-17:]


def _source(value: str) -> str:
    value = str(value or "user").strip().lower()
    return value if value in VALID_SOURCES else "user"


def _cited_agents(agent_refs: list[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in agent_refs or []:
        ref_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
        agent = get_agent(ref_id) if ref_id else None
        if agent and ref_id not in seen:
            seen.add(ref_id)
            result.append(agent)
    return result


def _effective_tools(
    agent: dict[str, Any],
    history: list[dict[str, str]],
    prompt: str,
    parent_agent_id: str | None = None,
) -> list[str]:
    del history, prompt
    own_skill_tools = tools_for_skills(list(agent.get("skills") or []))
    effective = normalize_tool_ids([*list(agent.get("tools") or []), *own_skill_tools])
    for scope in approved_scopes(str(agent.get("id") or "")):
        tool_id = tool_for_scope(scope)
        if tool_id and tool_id not in effective:
            effective.append(tool_id)
    if parent_agent_id:
        parent = get_agent(parent_agent_id)
        if parent:
            parent_skill_tools = tools_for_skills(list(parent.get("skills") or []))
            parent_declared = normalize_tool_ids([*list(parent.get("tools") or []), *parent_skill_tools])
            for tool_id in inheritable_tool_ids(parent_declared):
                if tool_id not in effective:
                    effective.append(tool_id)
    return effective


def _effective_scopes(
    agent: dict[str, Any],
    effective: list[str],
    parent_agent_id: str | None = None,
) -> set[str]:
    scopes = approved_scopes(str(agent.get("id") or ""))
    skill_ids = list(agent.get("skills") or [])
    own_skill_tools = tools_for_skills(skill_ids)
    declared = normalize_tool_ids([*list(agent.get("tools") or []), *own_skill_tools])
    if "permissions" in agent:
        permanent = normalize_scopes(list(agent.get("permissions") or []))
    else:
        permanent = [*scopes_for_tools(declared), *scopes_for_skills(skill_ids)]
    scopes.update(permanent)

    if parent_agent_id:
        parent = get_agent(parent_agent_id)
        if parent:
            parent_skill_ids = list(parent.get("skills") or [])
            parent_skill_tools = tools_for_skills(parent_skill_ids)
            parent_declared = normalize_tool_ids([*list(parent.get("tools") or []), *parent_skill_tools])
            inherited_tools = inheritable_tool_ids(parent_declared)
            if inherited_tools:
                if "permissions" in parent:
                    parent_permanent = normalize_scopes(list(parent.get("permissions") or []))
                else:
                    parent_permanent = [*scopes_for_tools(parent_declared), *scopes_for_skills(parent_skill_ids)]
                inherited_valid = {
                    scope for tool_id in inherited_tools for scope in scopes_for_tool(tool_id)
                }
                scopes.update(scope for scope in parent_permanent if scope in inherited_valid)

    if str(agent.get("id") or "") == "general":
        scopes.add("actis.admin")
    valid = {scope for tool_id in set(effective) for scope in scopes_for_tool(tool_id)}
    if str(agent.get("id") or "") == "general":
        valid.add("actis.admin")
    return {scope for scope in scopes if scope in valid}


def _tool_instructions(effective: list[str], scopes: set[str]) -> str:
    catalog = list_tool_catalog()
    text = (
        "\n\nTOOLS DO ACTIS GEN: você sempre pode consultar actis_list_tools. "
        f"Tools declaradas/autorizadas neste run: {effective}. "
        f"Escopos autorizados: {sorted(scopes)}. "
        "Se precisar de uma capacidade não autorizada, peça aprovação usando EXATAMENTE uma linha "
        "ACTIS_APPROVAL_REQUEST: <scope>. Exemplos: files.read, files.write, terminal.exec, "
        "computer.view, computer.control, browser.read, browser.interact, pdf.read, pdf.write, spreadsheet.read, spreadsheet.write. Não invente scopes. "
        f"Catálogo: {json.dumps(catalog, ensure_ascii=False)}"
    )
    if "harness_computer" in effective:
        text += (
            "\n\nCOMPUTER USE: para enxergar a tela, use computer_observe. "
            "Essa tool devolve descrição visual e coordenadas físicas da tela. "
            "Depois use as ações do Harness Computer. Observe novamente após mudanças importantes."
        )
    if "harness_browser" in effective:
        text += (
            "\n\nBROWSER HARNESS: trate o navegador como estado persistente, não como uma sessão nova a cada pedido. "
            "No início de tarefas de navegação, consulte browser_list_tabs. Se já existir uma aba do site/domínio "
            "pedido, use browser_switch_tab nela e preserve essa sessão; não abra outra cópia do mesmo site. "
            "Para pedidos como 'abra', 'acesse' ou 'vá para', reutilize a aba correspondente; se não existir, "
            "navegue a aba atual com browser_goto. Páginas web e SPAs podem levar tempo para estabilizar. "
            "Não trate carregamento lento como falha imediata. Depois de browser_goto, use browser_wait_for_load; "
            "em interfaces dinâmicas use browser_wait_for_element ou browser_wait em intervalos curtos e então "
            "reobserve com browser_page_info ou browser_screenshot. Antes de digitar em campos com efeito (mensagem, "
            "formulário, publicação), confirme deterministicamente com browser_js que o campo correto e o alvo esperado "
            "estão ativos; screenshot sozinho não prova isso. Depois de qualquer ação que altere estado externo, faça uma "
            "verificação determinística com browser_js ou browser_page_info. Nunca diga que enviou, salvou, publicou, "
            "apagou ou concluiu algo sem essa verificação posterior. Se uma leitura ou espera falhar transitoriamente, "
            "aguarde e tente novamente antes de desistir. Não repita automaticamente ações com efeito como click, "
            "type, fill ou upload sem reobservar o estado da página, para evitar duplicidade."
        )
    text += skill_instructions_for_tools(effective)
    return text


def _agent_instructions(
    agent: dict[str, Any], effective: list[str], cited_agents: list[dict[str, Any]], scopes: set[str],
    activation_text: str,
) -> str:
    skill_text = progressive_skill_context(list(agent.get("skills") or []), activation_text)
    own_text = str(agent.get("instructions") or "").strip()
    instructions = "\n\n".join(part for part in (skill_text, own_text) if part)
    if cited_agents:
        refs = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "model": item.get("model"),
                "company_id": item.get("company_id"),
                "sector_id": item.get("sector_id"),
            }
            for item in cited_agents
        ]
        instructions += (
            "\n\nAGENTES CITADOS NA MENSAGEM: "
            + json.dumps(refs, ensure_ascii=False)
            + ". Tokens /id no texto são referências explícitas a esses agentes. "
            "Uma citação identifica o agente como contexto; não é delegação automática."
        )
    instructions += (
        "\n\nEXECUÇÃO ORIENTADA A OBJETIVO: trate a mensagem atual do usuário como um objetivo que precisa de "
        "estado verificável, não como algo que basta reconhecer. Quando houver pedido de ação, mantenha internamente "
        "este ciclo: ENTENDER → AGIR → VERIFICAR → ENTREGAR. Não encerre em ENTENDER. Respostas finais como 'ok', "
        "'certo', 'entendi', 'feito', 'pronto' ou equivalentes são inválidas se não trouxerem o resultado concreto. "
        "Frases como 'vou verificar', 'vou checar' ou 'vou fazer isso' descrevem intenção, não conclusão. Continue "
        "trabalhando na mesma Run até obter um resultado real, evidência verificável ou um bloqueio concreto que exija "
        "ação do usuário. Se usar uma tool, incorpore o que ela comprovou na resposta final. Antes de concluir, valide: "
        "(1) qual era o objetivo, (2) o que foi executado, (3) qual evidência/resultou disso, (4) se ainda falta algo."
    )
    instructions += _tool_instructions(effective, scopes)
    return instructions


def _runtime_tools(
    agent_id: str,
    runtime_features: set[str],
    effective: list[str],
    scopes: set[str],
    env_file: str | None,
    model: str,
    run_id: str,
    source: str,
    contact_scope: str = "",
    delivery_key: str = "",
    whatsapp_tool_profile: str = "full",
) -> list[object]:
    if contact_scope:
        from meuharness.domains.whatsapp_tools import build_whatsapp_tools
        profile = str(whatsapp_tool_profile or "full").strip().lower()
        native: list[object] = []
        if profile in {"operational", "full"}:
            from meuharness.company_bus import build_company_tools
            native.extend(build_company_tools(agent_id, run_id=run_id, env_file=env_file))
        wa_profile = "conversation" if profile in {"operational", "quote"} else profile
        native.extend(build_whatsapp_tools(
            agent_id, source=source, run_id=run_id, contact_scope=contact_scope,
            delivery_key=delivery_key, profile=wa_profile,
        ))
        if profile in {"operational", "full"} and "colorglass.operations.local" in runtime_features:
            from meuharness.domains.colorglass_operations import build_colorglass_operation_tools
            native.extend(build_colorglass_operation_tools(agent_id))
        if profile in {"operational", "quote", "full"} and "colorglass.quotation.native" in runtime_features:
            from meuharness.colorglass_fast_tools import build_colorglass_fast_tools
            native.extend(build_colorglass_fast_tools(agent_id, run_id=run_id))
        return [observe_native_tool(tool, run_id=run_id, agent_id=agent_id) for tool in native]
    tools: list[object] = build_public_tools(agent_id=agent_id, run_id=run_id, env_file=env_file)
    tools.extend(build_connector_tools(agent_id, env_file=env_file, run_id=run_id))
    if "whatsapp.local" in runtime_features:
        from meuharness.domains.whatsapp_tools import build_whatsapp_tools
        tools.extend(build_whatsapp_tools(agent_id, source=source, run_id=run_id))
    if "logistics.erp.local" in runtime_features:
        from meuharness.domains.logistics_erp import build_logistics_tools
        tools.extend(build_logistics_tools(agent_id, source=source))
    if "colorglass.operations.local" in runtime_features:
        from meuharness.domains.colorglass_operations import build_colorglass_operation_tools
        tools.extend(build_colorglass_operation_tools(agent_id))
    if "colorglass.procurement.local" in runtime_features:
        from meuharness.domains.colorglass_procurement import build_procurement_tools
        tools.extend(build_procurement_tools(agent_id, run_id=run_id, env_file=env_file))
    if "colorglass.finance.local" in runtime_features:
        from meuharness.domains.colorglass_finance import build_finance_tools
        tools.extend(build_finance_tools(agent_id))
    if "actis_admin" in effective:
        from meuharness.control_tools import build_general_tools

        tools.extend(build_general_tools(env_file, run_id=run_id, owner_agent_id=agent_id))
    if "pdf_toolkit" in effective:
        tools.extend(build_pdf_tools(scopes, run_id=run_id, agent_id=agent_id))
    if "skill_excel" in effective:
        tools.extend(build_excel_tools(scopes, run_id=run_id, agent_id=agent_id))
    if "colorglass.quotation.native" in runtime_features:
        from meuharness.colorglass_fast_tools import build_colorglass_fast_tools
        from meuharness.colorglass_quotation_tools import build_colorglass_quotation_tools
        from meuharness.colorglass_session_tools import build_colorglass_session_tools
        tools.extend(build_colorglass_fast_tools(agent_id, run_id=run_id))
        tools.extend(build_colorglass_session_tools(agent_id))
        tools.extend(build_colorglass_quotation_tools(agent_id, run_id=run_id))
    if "harness_browser" in effective:
        if "browser.native_transport" in runtime_features:
            from meuharness.native_browser_tools import build_native_browser_tools
            tools.extend(build_native_browser_tools(agent_id))
        else:
            tools.append(build_harness_browser(scopes, run_id=run_id, agent_id=agent_id))
    if "harness_files" in effective:
        tools.append(build_harness_files(scopes, run_id=run_id, agent_id=agent_id))
    if "harness_terminal" in effective:
        tools.append(build_harness_terminal(run_id=run_id, agent_id=agent_id))
    if "harness_computer" in effective:
        tools.append(build_harness_computer(scopes, run_id=run_id, agent_id=agent_id))
        if "computer.view" in scopes or "computer.control" in scopes:
            tools.append(build_computer_observer(env_file, model))
    return [observe_native_tool(tool, run_id=run_id, agent_id=agent_id) for tool in tools]


async def _execution_heartbeat(run_id: str, agent_id: str, subsystem: str) -> None:
    while True:
        await asyncio.sleep(10)
        emit_event("run.heartbeat", run_id=run_id, agent_id=agent_id, subsystem=subsystem)


async def execute_agent(
    agent_id: str,
    prompt: str,
    *,
    history: list[dict[str, Any]] | None = None,
    source: str = "user",
    parent_agent_id: str | None = None,
    conversation_id: str | None = None,
    agent_refs: list[Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    env_file: str | None = None,
    resume_from_run_id: str | None = None,
    whatsapp_contact: str = "",
    whatsapp_delivery_key: str = "",
    whatsapp_tool_profile: str = "full",
    disable_tools: bool = False,
) -> AgentExecutionResult:
    """Execute one agent through the canonical ACTIS GEN lifecycle."""
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado.")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Mensagem vazia.")
    history_clean = _normalized_history(history, prompt)
    source = _source(source)
    if whatsapp_contact and source != "automation":
        raise ValueError("Escopo de atendimento recebido exige source=automation")
    model = str(agent.get("model") or "").strip()
    settings = NineRouterSettings.from_env(env_file, model=model or None)
    effective = _effective_tools(agent, history_clean, prompt, parent_agent_id)
    scopes = _effective_scopes(agent, effective, parent_agent_id)
    if disable_tools:
        effective = []
        scopes = set()
    uses_computer = "harness_computer" in effective
    uses_browser = "harness_browser" in effective
    if uses_computer:
        active = active_computer_run_ids()
        if active:
            raise ValueError(
                "Harness Computer ocupado por uma execução ativa. Pare a run ativa antes de iniciar outra."
            )
    desired_timeout = 300 if (uses_computer or uses_browser) else (
        120 if source in {"automation", "automation_condition", "delegation"} else 0
    )
    if desired_timeout and settings.timeout_s < desired_timeout:
        settings = NineRouterSettings.from_env(env_file, model=model or None, timeout_s=desired_timeout)
    context_package = resolve_agent_context(agent, prompt)
    project_memory_items = project_memory_for_agent(agent_id, prompt)
    runtime_features = runtime_features_for_skills(list(agent.get("skills") or []))
    if whatsapp_contact and "whatsapp.local" not in runtime_features:
        raise ValueError("Agente sem capability WhatsApp para atender este contato")
    activation_text = "\n".join(item["content"] for item in history_clean[-8:])
    channel_items = [] if whatsapp_contact else channel_context_for_agent(agent, prompt)
    instructions = _agent_instructions(
        agent, effective, _cited_agents(agent_refs), scopes, activation_text
    ) + context_instructions(context_package) + project_memory_instructions(project_memory_items) + channel_context_instructions(channel_items)
    from meuharness.company_bus import company_runtime_instructions
    instructions += company_runtime_instructions(agent_id)
    pdf_context = pdf_attachment_context(list(attachments or []))
    if pdf_context:
        instructions += pdf_context
    connector_catalog = [] if (whatsapp_contact or disable_tools) else connector_runtime_catalog(agent_id)
    if connector_catalog:
        instructions += "\n\nCONNECTORS EXECUTÁVEIS AUTORIZADOS:\n" + json.dumps(connector_catalog, ensure_ascii=False)
    if resume_from_run_id:
        instructions += (
            "\n\nRETOMADA DURÁVEL: esta Run continua uma execução anterior. "
            "Use os checkpoints abaixo como evidência já persistida. Não repita uma ação externa já concluída "
            "sem primeiro reobservar/verificar o estado atual; prefira idempotency_key em writes de connectors.\n"
            + recovery_summary(resume_from_run_id)
        )
    if "actis.state" in runtime_features:
        from meuharness.control_tools import general_state_snapshot
        instructions += "\n\nESTADO VIVO DO ACTIS GEN NO INÍCIO DESTA RUN:\n" + json.dumps(
            general_state_snapshot(), ensure_ascii=False
        )
    if "whatsapp.local" in runtime_features:
        from meuharness.domains.whatsapp_shadow import shadow_context
        local_shadow = shadow_context(agent_id, contact_name=whatsapp_contact or None)
        instructions += (
            "\n\nWHATSAPP LOCAL-FIRST: consulte whatsapp_local_search, whatsapp_local_recent e "
            "whatsapp_local_contacts antes de navegar quando a informação local puder responder. "
            "Use o browser para sincronizar/verificar frescor ou executar ações externas. Nunca trate outbox como "
            "enviada sem confirmação real. Estado local atual:\n" + json.dumps(local_shadow, ensure_ascii=False)
        )
    if agent.get("memory", True) and not whatsapp_contact:
        memories = recall(agent_id, prompt, limit=6)
        if memories:
            memory_text = "\n".join(f"- {item['content']}" for item in memories)
            instructions += (
                "\n\nMEMÓRIA PERSISTENTE RELEVANTE DO AGENTE:\n" + memory_text
                + "\nUse como contexto auxiliar; se conflitar com a mensagem atual, priorize a mensagem atual."
            )

    run = start_run(
        agent_id,
        prompt,
        settings.model,
        source=source,
        parent_agent_id=parent_agent_id,
        conversation_id=conversation_id,
        resume_of=resume_from_run_id,
    )
    append_checkpoint(
        run["id"], agent_id, "run", status="started",
        detail="Run iniciada" if not resume_from_run_id else f"Retomada de {resume_from_run_id}",
        payload={"source": source, "resume_of": resume_from_run_id},
        idempotency_key="run.started",
    )
    create_task_for_run(run)
    emit_event(
        "run.started",
        run_id=run["id"],
        agent_id=agent_id,
        source=source,
        conversation_id=conversation_id,
        model=settings.model,
    )
    set_run_activity(run["id"], agent_id, "thinking", detail="Interpretando tarefa")
    clear_run_tool_trace(run["id"])

    if _fast_colorglass_quote_route(agent_id, prompt, source, attachments):
        route_started = datetime.now(UTC)
        append_checkpoint(
            run["id"], agent_id, "delegation", status="started",
            detail="Roteamento determinístico para Orçamentos ColorGlass",
            payload={"target_agent_id": "or-amentos-colorglass", "router": "colorglass_quote_fast_v1"},
        )
        set_run_activity(run["id"], agent_id, "waiting_agent", detail="Orçamentos ColorGlass")
        try:
            child = await execute_agent(
                "or-amentos-colorglass", prompt, history=history_clean[:-1][-16:],
                source="delegation", parent_agent_id=agent_id, conversation_id=conversation_id,
                attachments=attachments, env_file=env_file,
            )
            child_run = get_run(child.run_id) or {}
            child_status = str(child_run.get("status") or "completed")
            child_artifacts = list_run_attachments(child.run_id)
            if child_artifacts:
                bubbled = bubble_run_artifacts(child.run_id, run["id"], agent_id)
                append_checkpoint(
                    run["id"], agent_id, "artifact", status="completed",
                    detail=f"{len(bubbled)} artifact(s) propagado(s) da Run filha",
                    payload={"child_run_id": child.run_id, "artifact_ids": [item.get("id") for item in bubbled]},
                )
            elapsed_ms = (datetime.now(UTC) - route_started).total_seconds() * 1000.0
            append_checkpoint(
                run["id"], agent_id, "delegation",
                status="completed" if child_status == "completed" else child_status,
                detail=f"Orçamentos ColorGlass → {child_status}",
                evidence=f"child_run={child.run_id}; {str(child.text or '')[:420]}",
                payload={"child_run_id": child.run_id, "child_status": child_status},
            )
            parent_status = child_status if child_status in {"completed", "blocked", "planned", "cancelled", "failed"} else "completed"
            if parent_status == "completed":
                append_checkpoint(run["id"], agent_id, "verify", status="completed", detail="Resultado do agente de domínio verificado", evidence=str(child.text or "")[:500])
            finish_run(run["id"], response=child.text, elapsed_ms=elapsed_ms, status=parent_status)
            update_task_by_run(
                run["id"], status=parent_status,
                activity="completed" if parent_status == "completed" else parent_status,
                detail="Concluído via roteamento determinístico ColorGlass" if parent_status == "completed" else f"ColorGlass retornou {parent_status}",
                tool="or-amentos-colorglass", finished_at=_task_finished_at() if parent_status in {"completed", "failed", "cancelled"} else None,
            )
            update_work_by_run(
                run["id"], phase="completed" if parent_status == "completed" else parent_status,
                detail="Roteamento direto para agente de orçamento",
                evidence=f"Run filha {child.run_id}: {parent_status}",
            )
            set_run_activity(run["id"], agent_id, "completed" if parent_status == "completed" else parent_status, detail=f"ColorGlass: {parent_status}")
            emit_event(
                "delegation.fast_completed", run_id=run["id"], agent_id=agent_id,
                target_agent_id="or-amentos-colorglass", child_run_id=child.run_id, status=parent_status,
            )
            clear_run_tool_trace(run["id"])
            return AgentExecutionResult(
                text=child.text, model=child.model, elapsed_ms=elapsed_ms, run_id=run["id"], effective_tools=tuple(effective),
            )
        except Exception as exc:
            append_checkpoint(run["id"], agent_id, "delegation", status="failed", detail=str(exc)[:500])
            finish_run(run["id"], error=str(exc))
            update_task_by_run(run["id"], status="failed", activity="failed", detail=str(exc)[:240], tool="or-amentos-colorglass", finished_at=_task_finished_at())
            set_run_activity(run["id"], agent_id, "failed", detail=str(exc)[:240])
            raise

    tools = [] if disable_tools else _runtime_tools(
        agent_id, runtime_features, effective, scopes, env_file, settings.model, run["id"],
        source, whatsapp_contact, whatsapp_delivery_key, whatsapp_tool_profile
    )
    _register_execution(run["id"], agent_id, computer=uses_computer)
    heartbeat_subsystem = "computer" if uses_computer else ("browser" if uses_browser else None)
    heartbeat = (
        asyncio.create_task(_execution_heartbeat(run["id"], agent_id, heartbeat_subsystem))
        if heartbeat_subsystem else None
    )
    try:
        harness = create_harness(settings, tools=tools)
        result = await harness.run(
            HarnessRequest(
                prompt=prompt,
                context={"conversation": history_clean[:-1][-16:], "organizational_context": context_package},
                attachments=[a for a in list(attachments or []) if str(a.get("media_type") or "").startswith("image/")],
                instructions=instructions,
                timeout_s=settings.timeout_s,
                max_output_tokens=900,
                tool_choice=(
                    None
                    if disable_tools
                    else (
                        _fast_quote_tool_choice(agent_id, prompt)
                        or (
                            {"mode": "required", "required_function_name": "browser_page_info"}
                            if "browser.required_for_action" in runtime_features and _prompt_requires_action(prompt)
                            else None
                        )
                    )
                ),
            )
        )
        elapsed_ms = result.elapsed_ms
        for continuation_attempt in range(4):
            if not _response_is_unfinished_intent(
                run["id"], result.text, prompt=prompt,
                require_browser_tool="browser.required_for_action" in runtime_features,
                require_action_tool="actis.state" in runtime_features,
            ):
                break
            set_run_activity(
                run["id"], agent_id, "planned",
                detail="O agente declarou intenção; continuando automaticamente até obter resultado.",
            )
            emit_event(
                "run.continuing",
                run_id=run["id"], agent_id=agent_id, source=source,
                conversation_id=conversation_id, attempt=continuation_attempt + 1,
            )
            update_work_by_run(
                run["id"], phase="acting",
                detail="Executando o que foi prometido na resposta intermediária",
            )
            continuation_prompt = (
                "Continue a tarefa original agora. Sua resposta anterior apenas descreveu o que você faria: "
                + str(result.text or "")[:900]
                + "\nNão anuncie um próximo passo. Execute as ações necessárias usando as tools disponíveis, "
                "verifique o resultado quando aplicável e só então entregue uma resposta final com o resultado real. "
                "Se houver um bloqueio concreto, explique o bloqueio em vez de prometer continuar depois."
            )
            continuation_context = [
                *history_clean[-15:],
                {"role": "assistant", "content": str(result.text or "")},
            ][-16:]
            next_result = await harness.run(
                HarnessRequest(
                    prompt=continuation_prompt,
                    context={"conversation": continuation_context, "organizational_context": context_package},
                    attachments=[],
                    instructions=instructions,
                    timeout_s=settings.timeout_s,
                    max_output_tokens=900,
                    tool_choice=(
                        {"mode": "required", "required_function_name": "browser_page_info"}
                        if "browser.required_for_action" in runtime_features
                        and not _has_browser_evidence(run["id"])
                        else None
                    ),
                )
            )
            if elapsed_ms is not None and next_result.elapsed_ms is not None:
                elapsed_ms += next_result.elapsed_ms
            elif next_result.elapsed_ms is not None:
                elapsed_ms = next_result.elapsed_ms
            result = next_result
    except asyncio.CancelledError:
        append_checkpoint(run["id"], agent_id, "run", status="cancelled", detail="Cancelada pelo usuário")
        cancel_run_record(run["id"])
        update_task_by_run(
            run["id"],
            status="cancelled",
            activity="blocked",
            detail="Cancelada pelo usuário",
            tool=None,
            finished_at=_task_finished_at(),
        )
        set_run_activity(run["id"], agent_id, "blocked", detail="Cancelada pelo usuário")
        emit_event(
            "run.cancelled",
            run_id=run["id"],
            agent_id=agent_id,
            source=source,
            conversation_id=conversation_id,
        )
        exc = RuntimeError("Execução cancelada pelo usuário.")
        exc.actis_run_id = run["id"]
        raise exc from None
    except Exception as exc:
        append_checkpoint(run["id"], agent_id, "run", status="failed", detail=str(exc)[:500])
        finish_run(run["id"], error=str(exc))
        update_task_by_run(
            run["id"],
            status="failed",
            activity="failed",
            detail=str(exc)[:240],
            tool=None,
            finished_at=_task_finished_at(),
        )
        set_run_activity(run["id"], agent_id, "failed", detail=str(exc)[:240])
        try:
            exc.actis_run_id = run["id"]
        except (AttributeError, TypeError):
            pass
        emit_event(
            "run.failed",
            run_id=run["id"],
            agent_id=agent_id,
            source=source,
            conversation_id=conversation_id,
            error_type=type(exc).__name__,
        )
        clear_run_tool_trace(run["id"])
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        _unregister_execution(run["id"])

    if _response_is_unfinished_intent(
        run["id"], result.text, prompt=prompt,
        require_browser_tool="browser.required_for_action" in runtime_features,
        require_action_tool="actis.state" in runtime_features,
    ):
        cleaned_text = re.sub(
            r"(?mi)^\s*ACTIS_APPROVAL_REQUEST:\s*[a-z_.]+\s*$", "", result.text
        ).strip() or "A tarefa ainda não foi executada."
        append_checkpoint(
            run["id"], agent_id, "run", status="planned",
            detail="Resposta continha apenas intenção; retomada necessária",
            evidence=cleaned_text[:500],
        )
        finish_run(run["id"], response=cleaned_text, elapsed_ms=elapsed_ms, status="planned")
        update_task_by_run(
            run["id"], status="planned", activity="planned",
            detail="O agente ainda descreveu intenção em vez de entregar o resultado.", tool=None,
        )
        update_work_by_run(
            run["id"], phase="planned",
            detail="Retomar a execução até produzir resultado ou bloqueio concreto",
            evidence="Conclusão recusada: resposta continha apenas intenção",
        )
        set_run_activity(
            run["id"], agent_id, "planned",
            detail="Conclusão recusada: falta executar o próximo passo",
        )
        emit_event(
            "run.planned", run_id=run["id"], agent_id=agent_id, source=source,
            conversation_id=conversation_id, model=result.model,
        )
        clear_run_tool_trace(run["id"])
        return AgentExecutionResult(
            text=cleaned_text, model=result.model, elapsed_ms=elapsed_ms,
            run_id=run["id"], effective_tools=tuple(effective),
        )

    if _required_browser_action_missing(run["id"], prompt, runtime_features):
        cleaned_text = re.sub(
            r"(?mi)^\s*ACTIS_APPROVAL_REQUEST:\s*[a-z_.]+\s*$", "", result.text
        ).strip() or "A ação no navegador não foi executada."
        append_checkpoint(
            run["id"], agent_id, "verify", status="blocked",
            detail="A tarefa exigia ação no navegador, mas nenhuma ação de browser foi concluída",
            evidence=cleaned_text[:500],
        )
        finish_run(run["id"], response=cleaned_text, elapsed_ms=elapsed_ms, status="blocked")
        update_task_by_run(
            run["id"], status="blocked", activity="blocked",
            detail="Nenhuma ação de browser concluída", tool=None,
        )
        update_work_by_run(
            run["id"], phase="blocked",
            detail="Executar uma ação real no navegador antes de concluir",
            evidence="Conclusão recusada: nenhuma ação de browser concluída",
        )
        set_run_activity(run["id"], agent_id, "blocked", detail="Ação de browser não executada")
        emit_event(
            "browser.action_missing", run_id=run["id"], agent_id=agent_id, source=source,
            conversation_id=conversation_id,
        )
        clear_run_tool_trace(run["id"])
        return AgentExecutionResult(
            text=cleaned_text, model=result.model, elapsed_ms=elapsed_ms,
            run_id=run["id"], effective_tools=tuple(effective),
        )

    requested_scopes = set(re.findall(r"ACTIS_APPROVAL_REQUEST:\s*([a-z_.]+)", result.text))
    valid_scopes = {scope for values in TOOL_SCOPES.values() for scope in values}
    cleaned_text = re.sub(r"(?mi)^\s*ACTIS_APPROVAL_REQUEST:\s*[a-z_.]+\s*$", "", result.text).strip()
    if requested_scopes and not cleaned_text:
        cleaned_text = "Solicitação de permissão enviada para Approvals."
    set_run_activity(run["id"], agent_id, "verifying", detail="Verificando se o objetivo foi realmente concluído")
    verification_missing = uses_browser and not _browser_success_is_verified(run["id"], cleaned_text)
    if verification_missing:
        cleaned_text = (
            "A ação foi tentada no navegador, mas não houve verificação determinística depois da última "
            "interação. Não vou afirmar que ela foi concluída. Reabra/observe o estado da página e confirme "
            "o resultado antes de tentar novamente."
        )
        emit_event(
            "browser.verification_missing",
            run_id=run["id"], agent_id=agent_id, source=source,
            conversation_id=conversation_id,
        )
        append_checkpoint(
            run["id"], agent_id, "verify", status="blocked",
            detail="Falta verificação determinística pós-ação",
            evidence=cleaned_text[:500],
        )
        finish_run(run["id"], response=cleaned_text, elapsed_ms=elapsed_ms, status="blocked")
        update_task_by_run(
            run["id"], status="blocked", activity="blocked",
            detail="Falta verificação determinística do resultado", tool=None,
        )
        update_work_by_run(
            run["id"], phase="blocked",
            detail="Verificar o estado real depois da última ação no navegador",
            evidence="Conclusão recusada: verificação pós-ação ausente",
        )
        set_run_activity(run["id"], agent_id, "blocked", detail="Resultado ainda não verificado")
        emit_event(
            "run.blocked", run_id=run["id"], agent_id=agent_id, source=source,
            conversation_id=conversation_id, model=result.model,
        )
        clear_run_tool_trace(run["id"])
        return AgentExecutionResult(
            text=cleaned_text, model=result.model, elapsed_ms=elapsed_ms,
            run_id=run["id"], effective_tools=tuple(effective),
        )
    append_checkpoint(
        run["id"], agent_id, "verify", status="completed",
        detail="Resultado final aceito", evidence=cleaned_text[:500],
    )
    finish_run(run["id"], response=cleaned_text, elapsed_ms=elapsed_ms)
    valid_requested = sorted(requested_scopes & valid_scopes)
    for scope in valid_requested:
        approval = request_approval(agent_id, scope, reason=f"Solicitado na run {run['id']}")
        emit_event(
            "approval.requested",
            approval_id=approval["id"],
            agent_id=agent_id,
            scope=scope,
            run_id=run["id"],
        )
    if valid_requested:
        update_task_by_run(run["id"], status="waiting", activity="waiting_approval", detail="Aguardando aprovação: " + ", ".join(valid_requested), tool=None)
        set_run_activity(run["id"], agent_id, "waiting_approval", detail="Aguardando aprovação: " + ", ".join(valid_requested))
    else:
        evidence_items = _tool_evidence(run["id"])
        if not evidence_items:
            evidence_items = ["Saída final registrada: " + cleaned_text[:180].replace("\n", " ")]
        for evidence_item in evidence_items:
            update_work_by_run(run["id"], evidence=evidence_item)
        update_task_by_run(
            run["id"], status="completed", activity="completed", detail="Tarefa concluída",
            tool=None, finished_at=_task_finished_at(),
        )
        set_run_activity(run["id"], agent_id, "completed", detail="Objetivo concluído e resultado entregue")
    if agent.get("memory", True):
        remember(
            agent_id,
            f"Usuário: {prompt[:1200]} | Assistente: {cleaned_text[:1800]}",
            conversation_id=conversation_id,
        )
    emit_event(
        "run.completed",
        run_id=run["id"],
        agent_id=agent_id,
        source=source,
        conversation_id=conversation_id,
        elapsed_ms=elapsed_ms,
        model=result.model,
    )
    clear_run_tool_trace(run["id"])
    return AgentExecutionResult(
        text=cleaned_text,
        model=result.model,
        elapsed_ms=elapsed_ms,
        run_id=run["id"],
        effective_tools=tuple(effective),
    )


async def resume_run(run_id: str, *, env_file: str | None = None) -> AgentExecutionResult:
    """Resume a durable Run as a new attempt linked to the previous execution."""
    previous = get_run(run_id)
    if not previous:
        raise ValueError("Run não encontrada.")
    if previous.get("status") == "completed":
        raise ValueError("Run já concluída; não há o que retomar.")
    if previous.get("status") == "running":
        raise ValueError("Run ainda está ativa; cancele ou aguarde antes de retomar.")
    prompt = str(previous.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Run anterior não possui prompt recuperável.")
    history: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    previous_text = str(previous.get("response") or previous.get("error") or "").strip()
    if previous_text:
        history.append({"role": "assistant", "content": previous_text})
    return await execute_agent(
        str(previous.get("agent_id") or ""),
        prompt,
        history=history,
        source="resume",
        parent_agent_id=previous.get("parent_agent_id"),
        conversation_id=previous.get("conversation_id"),
        env_file=env_file,
        resume_from_run_id=run_id,
    )
