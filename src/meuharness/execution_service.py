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
from meuharness.adapters.maf.system_tools import (
    build_computer_observer,
    build_harness_computer,
    build_harness_files,
    build_harness_terminal,
)
from meuharness.agents import cancel_run_record, finish_run, get_agent, start_run
from meuharness.approvals import (
    TOOL_SCOPES,
    approved_scopes,
    request_approval,
    scopes_for_tool,
    tool_for_scope,
)
from meuharness.bootstrap import create_harness
from meuharness.contexts import context_instructions, resolve_agent_context
from meuharness.event_bus import emit_event
from meuharness.memory import recall, remember
from meuharness.observability import set_run_activity
from meuharness.providers.nine_router import NineRouterSettings
from meuharness.public_tools import build_public_tools
from meuharness.tasks import create_task_for_run, update_task_by_run
from meuharness.tool_registry import (
    inheritable_tool_ids,
    list_tool_catalog,
    normalize_scopes,
    normalize_tool_ids,
    scopes_for_tools,
    skill_instructions_for_tools,
)

VALID_SOURCES = {"user", "direct", "delegation", "automation", "automation_condition"}
AFFIRMATIVE = {"sim", "pode", "pode sim", "autorizo", "autorizado", "ok", "claro"}

_ACTIVE_LOCK = threading.RLock()
_ACTIVE_EXECUTIONS: dict[str, dict[str, Any]] = {}


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
    effective = normalize_tool_ids(list(agent.get("tools") or []))
    for scope in approved_scopes(str(agent.get("id") or "")):
        tool_id = tool_for_scope(scope)
        if tool_id and tool_id not in effective:
            effective.append(tool_id)
    if parent_agent_id:
        parent = get_agent(parent_agent_id)
        if parent:
            for tool_id in inheritable_tool_ids(list(parent.get("tools") or [])):
                if tool_id not in effective:
                    effective.append(tool_id)
    return effective


def _effective_scopes(
    agent: dict[str, Any],
    effective: list[str],
    parent_agent_id: str | None = None,
) -> set[str]:
    scopes = approved_scopes(str(agent.get("id") or ""))
    declared = normalize_tool_ids(list(agent.get("tools") or []))
    if "permissions" in agent:
        permanent = normalize_scopes(list(agent.get("permissions") or []))
    else:
        permanent = scopes_for_tools(declared)
    scopes.update(permanent)

    if parent_agent_id:
        parent = get_agent(parent_agent_id)
        if parent:
            parent_declared = normalize_tool_ids(list(parent.get("tools") or []))
            inherited_tools = inheritable_tool_ids(parent_declared)
            if inherited_tools:
                if "permissions" in parent:
                    parent_permanent = normalize_scopes(list(parent.get("permissions") or []))
                else:
                    parent_permanent = scopes_for_tools(parent_declared)
                inherited_valid = {
                    scope
                    for tool_id in inherited_tools
                    for scope in scopes_for_tool(tool_id)
                }
                scopes.update(scope for scope in parent_permanent if scope in inherited_valid)

    if str(agent.get("id") or "") == "general":
        scopes.add("actis.admin")
    allowed_tools = set(effective)
    valid = {scope for tool_id in allowed_tools for scope in scopes_for_tool(tool_id)}
    valid.add("actis.admin")
    return {scope for scope in scopes if scope in valid}


def _tool_instructions(effective: list[str], scopes: set[str]) -> str:
    catalog = list_tool_catalog()
    text = (
        "\n\nTOOLS DO ACTIS GEN: você sempre pode consultar actis_list_tools. "
        f"Tools declaradas/autorizadas/herdadas neste run: {effective}. "
        f"Escopos autorizados: {sorted(scopes)}. "
        "Se precisar de uma capacidade não autorizada, peça aprovação usando EXATAMENTE uma linha "
        "ACTIS_APPROVAL_REQUEST: <scope>. Exemplos: files.read, files.write, terminal.exec, "
        "computer.view, computer.control, browser.read, browser.interact, spreadsheet.read, "
        "spreadsheet.write. Não invente scopes. "
        f"Catálogo: {json.dumps(catalog, ensure_ascii=False)}"
    )
    if "harness_computer" in effective:
        text += (
            "\n\nCOMPUTER USE: para enxergar a tela, use computer_observe. "
            "Essa tool devolve descrição visual e coordenadas físicas da tela. "
            "Depois use as ações do Harness Computer. Observe novamente após mudanças importantes."
        )
    text += skill_instructions_for_tools(effective)
    return text


def _agent_instructions(
    agent: dict[str, Any], effective: list[str], cited_agents: list[dict[str, Any]], scopes: set[str]
) -> str:
    instructions = str(agent.get("instructions") or "")
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
    instructions += _tool_instructions(effective, scopes)
    if agent.get("id") == "general":
        from meuharness.control_tools import general_state_snapshot

        live_state = general_state_snapshot()
        instructions += (
            "\n\nPAPEL DO GENERAL: você é o plano de controle administrativo do ACTIS GEN. "
            "Tudo que existe na interface administrativa deve ser operável por você pelas tools actis_*. "
            "Isso inclui: agentes, criação/edição/alocação entre empresas e setores, empresas, setores, "
            "projetos e contexto hierárquico, bindings de contexto, modelos, runs, tarefas/World, eventos, "
            "approvals, automações, workflows completos (criar/editar/validar/executar/excluir), conversas e delegação. "
            "Quando o usuário pedir uma ação, faça a alteração em vez de apenas explicar. "
            "Antes de mutações dependentes de IDs/estado, consulte actis_get_system_state ou a listagem específica se houver dúvida. "
            "Nunca invente IDs, empresas, setores, agentes, workflows ou capacidades. "
            "Ao transferir um agente, use actis_update_agent com company_id/sector_id. "
            "Ao criar workflow, produza nós/conexões válidos e confira a validação retornada. "
            "Se uma ação não tiver tool administrativa correspondente, diga objetivamente que ainda não está exposta no plano de controle. "
            "\n\nESTADO VIVO DO ACTIS GEN NO INÍCIO DESTA RUN:\n"
            + json.dumps(live_state, ensure_ascii=False)
        )
    return instructions


def _runtime_tools(
    agent_id: str,
    effective: list[str],
    scopes: set[str],
    env_file: str | None,
    model: str,
    run_id: str,
) -> list[object]:
    tools: list[object] = build_public_tools()
    if agent_id == "general" or "actis_admin" in effective:
        from meuharness.control_tools import build_general_tools

        tools.extend(build_general_tools(env_file, run_id=run_id))
    if "harness_browser" in effective:
        tools.append(build_harness_browser(scopes, run_id=run_id, agent_id=agent_id))
    if "harness_files" in effective:
        tools.append(build_harness_files(scopes, run_id=run_id, agent_id=agent_id))
    if "harness_terminal" in effective:
        tools.append(build_harness_terminal(run_id=run_id, agent_id=agent_id))
    if "harness_computer" in effective:
        tools.append(build_harness_computer(scopes, run_id=run_id, agent_id=agent_id))
        if "computer.view" in scopes or "computer.control" in scopes:
            tools.append(build_computer_observer(env_file, model))
    if "skill_excel" in effective:
        from meuharness.spreadsheet_tools import build_excel_tools

        tools.extend(build_excel_tools(scopes, run_id=run_id, agent_id=agent_id))
    return tools


async def _computer_heartbeat(run_id: str, agent_id: str) -> None:
    while True:
        await asyncio.sleep(10)
        emit_event("run.heartbeat", run_id=run_id, agent_id=agent_id, subsystem="computer")


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
    model = str(agent.get("model") or "").strip()
    settings = NineRouterSettings.from_env(env_file, model=model or None)
    effective = _effective_tools(agent, history_clean, prompt, parent_agent_id)
    scopes = _effective_scopes(agent, effective, parent_agent_id)
    uses_computer = "harness_computer" in effective
    if uses_computer:
        active = active_computer_run_ids()
        if active:
            raise ValueError(
                "Harness Computer ocupado por uma execução ativa. Pare a run ativa antes de iniciar outra."
            )
    if uses_computer and settings.timeout_s < 300:
        settings = NineRouterSettings.from_env(env_file, model=model or None, timeout_s=300)
    context_package = resolve_agent_context(agent, prompt)
    instructions = _agent_instructions(agent, effective, _cited_agents(agent_refs), scopes) + context_instructions(context_package)
    if agent.get("memory", True):
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
    tools = _runtime_tools(agent_id, effective, scopes, env_file, settings.model, run["id"])
    _register_execution(run["id"], agent_id, computer=uses_computer)
    heartbeat = asyncio.create_task(_computer_heartbeat(run["id"], agent_id)) if uses_computer else None
    try:
        result = await create_harness(settings, tools=tools).run(
            HarnessRequest(
                prompt=prompt,
                context={"conversation": history_clean[:-1][-16:], "organizational_context": context_package},
                attachments=list(attachments or []),
                instructions=instructions,
                timeout_s=settings.timeout_s,
                max_output_tokens=900,
            )
        )
    except asyncio.CancelledError:
        cancel_run_record(run["id"])
        update_task_by_run(run["id"], status="blocked", activity="blocked", detail="Cancelada pelo usuário")
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
        finish_run(run["id"], error=str(exc))
        update_task_by_run(run["id"], status="blocked", activity="failed", detail=str(exc)[:240])
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
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        _unregister_execution(run["id"])

    requested_scopes = set(re.findall(r"ACTIS_APPROVAL_REQUEST:\s*([a-z_.]+)", result.text))
    valid_scopes = {scope for values in TOOL_SCOPES.values() for scope in values}
    cleaned_text = re.sub(r"(?mi)^\s*ACTIS_APPROVAL_REQUEST:\s*[a-z_.]+\s*$", "", result.text).strip()
    if requested_scopes and not cleaned_text:
        cleaned_text = "Solicitação de permissão enviada para Approvals."
    finish_run(run["id"], response=cleaned_text, elapsed_ms=result.elapsed_ms)
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
        update_task_by_run(run["id"], status="completed", activity="completed", detail="Tarefa concluída", tool=None, finished_at=_task_finished_at())
        set_run_activity(run["id"], agent_id, "completed", detail="Tarefa concluída")
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
        elapsed_ms=result.elapsed_ms,
        model=result.model,
    )
    return AgentExecutionResult(
        text=cleaned_text,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
        run_id=run["id"],
        effective_tools=tuple(effective),
    )
