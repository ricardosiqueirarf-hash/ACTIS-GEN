"""Agent-callable tools for WhatsApp local state and deterministic transport."""
from __future__ import annotations

import functools
import inspect
import json

from meuharness.adapters.maf.observed_tool import record_tool_result
from meuharness.domains.whatsapp_dispatcher import (
    dispatch_outbox_item,
    is_delegated_operator_notification,
    open_session,
    send_document,
    send_message,
)
from meuharness.domains.whatsapp_shadow import (
    add_label,
    customer_context,
    get_conversation_by_contact,
    handoff_to_human,
    list_inbox,
    list_labels,
    list_monitored_contacts,
    outbox_item,
    pending_outbox,
    queue_message,
    recent_messages,
    remove_label,
    resume_automation,
    runtime_settings,
    save_customer_context,
    search_messages,
    set_automation_enabled,
    set_intent,
    update_conversation,
    upsert_contact,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _compact_operator_reply(value: str) -> str:
    """Keep the owner's test chat natural even if a model emits support boilerplate."""
    import re
    text = " ".join(str(value or "").split()).strip()
    text = re.sub(r"^ol[aá],?\s*ricardo[!,.]?\s*", "", text, flags=re.I)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    blocked = (
        "atendimento automático", "atendimento automatico", "sistema está ativo", "sistema esta ativo",
        "ativo e operacional", "ativo e autorizado", "recebi sua mensagem", "mensagem foi recebida",
        "caso precise", "caso deseje", "se precisar de algo", "se precisar testar",
        "se você tiver", "se voce tiver", "fique à vontade", "fique a vontade",
        "é só enviar", "e so enviar", "posso ajudar em algo", "como posso ajudá", "como posso ajuda",
        "por favor, informe o objetivo", "obrigado!", "obrigado.",
    )
    kept = [s for s in sentences if not any(token in s.casefold() for token in blocked)]
    text = " ".join(kept).strip() or "Beleza."
    if len(text) > 240:
        short = []
        size = 0
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            extra = len(sentence) + (1 if short else 0)
            if short and size + extra > 240:
                break
            short.append(sentence)
            size += extra
        text = " ".join(short).strip()
        if len(text) > 240:
            text = text[:237].rstrip() + "..."
    return text


def build_whatsapp_tools(agent_id: str, *, source: str = "user", run_id: str = "",
                         contact_scope: str = "", delivery_key: str = "",
                         profile: str = "full") -> list[object]:
    operator_source = source in {"user", "direct"}

    def message_key(contact: str, content: str) -> str | None:
        if delivery_key:
            return "reply:" + delivery_key
        return f"{run_id}:message:{contact}:{content}" if run_id else None

    def control_blocked() -> str:
        return _dump({"ok": False, "status": "blocked", "reason": "operator_action_required"})

    def whatsapp_open() -> str:
        """Abra/reutilize a sessão persistente e verifique autenticação; nunca autentique sozinho."""
        return _dump(open_session(agent_id))

    def whatsapp_sync_contact(contact: str) -> str:
        """Atualize SOMENTE este contato; verifique identidade e leia histórico sem enviar mensagens."""
        from meuharness.domains.whatsapp_sync import sync_once
        return _dump(sync_once(agent_id, contact_name=contact, current_run_id=run_id,
                               emit_inbound_events=False))

    def whatsapp_customer_context(contact: str) -> str:
        """Leia o resumo persistente, fatos, pendências, evidências e frescor de um cliente."""
        return _dump(customer_context(agent_id, contact))

    def whatsapp_save_context(contact: str, summary: str, current_subject: str = "",
                              pending_items_json: str = "[]", facts_json: str = "{}",
                              evidence_ids_json: str = "[]") -> str:
        """Salve resumo fiel; JSONs contêm pendências, fatos e IDs REAIS do histórico deste contato."""
        return _dump(save_customer_context(agent_id, contact, summary=summary,
            current_subject=current_subject, pending_items=json.loads(pending_items_json),
            facts=json.loads(facts_json), evidence_ids=json.loads(evidence_ids_json)))

    def whatsapp_local_search(query: str, contact: str = "", limit: int = 20) -> str:
        """Busque no histórico local sincronizado antes de abrir o WhatsApp Web."""
        return _dump(search_messages(agent_id, query, contact_name=contact or None, limit=limit))

    def whatsapp_local_recent(contact: str = "", limit: int = 30) -> str:
        """Leia mensagens recentes da cópia local sem navegar no browser."""
        return _dump(recent_messages(agent_id, contact_name=contact or None, limit=limit))

    def whatsapp_local_contacts() -> str:
        """Liste contatos/grupos configurados para sincronização local."""
        return _dump([row for row in list_monitored_contacts(agent_id) if not contact_scope or row["name"] == contact_scope])

    def whatsapp_monitor_contact(contact: str, phone: str = "", enabled: bool = True,
                                 sync_history: bool = False) -> str:
        """Configure contato/grupo monitorado; phone melhora abertura determinística por URL."""
        if not operator_source:
            return control_blocked()
        return _dump(upsert_contact(
            agent_id, contact, external_id=phone or None, monitored=enabled, sync_history=sync_history
        ))

    def whatsapp_automation_jobs(limit: int = 20) -> str:
        """Liste atendimentos recebidos, execuções, erros e vínculo com a outbox."""
        from meuharness.domains.whatsapp_automation import list_jobs
        return _dump([row for row in list_jobs(agent_id,limit) if not contact_scope or row["contact_name"] == contact_scope])

    def whatsapp_runtime_status() -> str:
        """Leia o kill switch global do atendimento automático deste agente."""
        return _dump(runtime_settings(agent_id))

    def whatsapp_set_automation(enabled: bool) -> str:
        """Ligue/desligue globalmente respostas automáticas; envios manuais explícitos continuam separados."""
        if not operator_source:
            return control_blocked()
        return _dump(set_automation_enabled(agent_id, enabled))

    def whatsapp_inbox(status: str = "", limit: int = 100) -> str:
        """Liste inbox local por lifecycle: active, pending, resolved ou archived."""
        return _dump([row for row in list_inbox(agent_id, status=status or None, limit=limit) if not contact_scope or row["contact_name"] == contact_scope])

    def whatsapp_set_conversation(contact: str, status: str = "", automation_mode: str = "",
                                  priority: str = "", assigned_agent: str = "") -> str:
        """Atualize lifecycle, modo de automação, prioridade ou owner de uma conversa."""
        if automation_mode and not operator_source:
            return control_blocked()
        return _dump(update_conversation(
            agent_id, contact,
            status=status or None,
            automation_mode=automation_mode or None,
            priority=priority or None,
            assigned_agent=assigned_agent if assigned_agent else None,
        ))

    def whatsapp_handoff(contact: str, reason: str = "") -> str:
        """Passe a conversa a humano e bloqueie respostas automáticas até liberação explícita."""
        return _dump(handoff_to_human(agent_id, contact, reason=reason))

    def whatsapp_resume(contact: str, mode: str = "supervised") -> str:
        """Devolva conversa ao agente em supervised ou autonomous."""
        if not operator_source:
            return control_blocked()
        return _dump(resume_automation(agent_id, contact, mode=mode))

    def whatsapp_set_intent(contact: str, intent: str, confidence: float = 0.0,
                            language: str = "") -> str:
        """Persista a intenção classificada para não reclassificar mensagens futuras."""
        return _dump(set_intent(agent_id, contact, intent,
                                confidence=confidence or None, language=language or None))

    def whatsapp_add_label(contact: str, label: str) -> str:
        """Adicione label local namespace:value (ex.: intent:orcamento)."""
        return _dump(add_label(agent_id, contact, label))

    def whatsapp_remove_label(contact: str, label: str) -> str:
        """Remova label namespace:value de uma conversa."""
        return _dump(remove_label(agent_id, contact, label))

    def whatsapp_labels(contact: str = "") -> str:
        """Liste labels locais de uma conversa ou do inbox inteiro."""
        return _dump(list_labels(agent_id, contact_name=contact or None))

    def whatsapp_queue_message(contact: str, content: str) -> str:
        """Registre mensagem na outbox sem afirmar que foi enviada."""
        return _dump(queue_message(agent_id, contact, content, source=source, idempotency_key=message_key(contact, content)))

    def whatsapp_send_message(contact: str, content: str) -> str:
        """Envie pelo dispatcher determinístico; sent só existe após ACK no DOM."""
        conversation = get_conversation_by_contact(agent_id, contact)
        mode = str((conversation or {}).get("automation_mode") or "supervised")
        if mode == "disabled":
            record_tool_result(run_id, "whatsapp_send_message", False)
            return _dump({"ok": False, "status": "blocked", "reason": "automation_disabled", "contact": contact})
        operator_notification = is_delegated_operator_notification(agent_id, contact, source)
        if source in {"automation", "automation_condition", "delegation"} and mode != "autonomous" and not operator_notification:
            record_tool_result(run_id, "whatsapp_send_message", False)
            return _dump({"ok": False, "status": "blocked", "reason": f"automation_mode:{mode}", "contact": contact})
        if source in {"automation", "automation_condition"} and contact == "85988241620":
            content = _compact_operator_reply(content)
        result = send_message(agent_id, contact, content, source=source, idempotency_key=message_key(contact, content))
        verified = result.get("status") == "sent"
        record_tool_result(run_id, "whatsapp_send_message", verified)
        return _dump({"ok": verified, "verified": verified, **result})

    def whatsapp_send_document(contact: str, path: str, filename: str = "", caption: str = "") -> str:
        """Envie um PDF/documento já gerado pelo ACTIS ao contato atual, com ACK verificado."""
        conversation = get_conversation_by_contact(agent_id, contact)
        mode = str((conversation or {}).get("automation_mode") or "supervised")
        if mode == "disabled":
            record_tool_result(run_id, "whatsapp_send_document", False)
            return _dump({"ok": False, "status": "blocked", "reason": "automation_disabled", "contact": contact})
        operator_notification = is_delegated_operator_notification(agent_id, contact, source)
        if source in {"automation", "automation_condition", "delegation"} and mode != "autonomous" and not operator_notification:
            record_tool_result(run_id, "whatsapp_send_document", False)
            return _dump({"ok": False, "status": "blocked", "reason": f"automation_mode:{mode}", "contact": contact})
        key = (
            f"reply:{delivery_key}:document:{filename or path}"
            if delivery_key else (f"{run_id}:document:{contact}:{filename or path}" if run_id else None)
        )
        result = send_document(
            agent_id, contact, path, filename=filename or None, caption=caption,
            source=source, idempotency_key=key,
        )
        verified = result.get("status") == "sent"
        record_tool_result(run_id, "whatsapp_send_document", verified)
        return _dump({"ok": verified, "verified": verified, **result})

    def whatsapp_dispatch_outbox(outbox_id: str) -> str:
        """Tente executar um item já enfileirado e retorne seu estado verificado."""
        item = outbox_item(outbox_id)
        if not item or item.get("agent_id") != agent_id:
            record_tool_result(run_id, "whatsapp_dispatch_outbox", False)
            return _dump({"ok": False, "error": "outbox_not_found"})
        if contact_scope and item.get("contact_name") != contact_scope:
            return control_blocked()
        if delivery_key and item.get("idempotency_key") != "reply:" + delivery_key:
            return control_blocked()
        if not operator_source and (item.get("conversation_automation_mode") != "autonomous" or not runtime_settings(agent_id)["automation_enabled"]):
            return control_blocked()
        result = dispatch_outbox_item(agent_id, outbox_id)
        record_tool_result(run_id, "whatsapp_dispatch_outbox", result.get("status") == "sent")
        return _dump(result)

    def whatsapp_pending_outbox(limit: int = 20) -> str:
        """Liste pending/failed/sending; não confunda esses estados com sent."""
        return _dump([row for row in pending_outbox(agent_id, limit=limit) if not contact_scope or row["contact_name"] == contact_scope])

    tools = [
        whatsapp_open, whatsapp_sync_contact, whatsapp_customer_context, whatsapp_save_context,
        whatsapp_local_search, whatsapp_local_recent, whatsapp_local_contacts, whatsapp_monitor_contact,
        whatsapp_runtime_status, whatsapp_set_automation, whatsapp_automation_jobs,
        whatsapp_inbox, whatsapp_set_conversation, whatsapp_handoff, whatsapp_resume, whatsapp_set_intent,
        whatsapp_add_label, whatsapp_remove_label, whatsapp_labels,
        whatsapp_queue_message, whatsapp_send_message, whatsapp_send_document, whatsapp_dispatch_outbox, whatsapp_pending_outbox,
    ]

    profile = str(profile or "full").strip().lower()
    if profile == "minimal":
        allowed = {"whatsapp_send_message", "whatsapp_send_document", "whatsapp_handoff"}
        tools = [tool for tool in tools if tool.__name__ in allowed]
    elif profile == "conversation":
        allowed = {
            "whatsapp_send_message", "whatsapp_send_document", "whatsapp_handoff", "whatsapp_customer_context",
            "whatsapp_local_recent", "whatsapp_local_search", "whatsapp_save_context",
        }
        tools = [tool for tool in tools if tool.__name__ in allowed]

    if not contact_scope:
        return tools

    def bind_contact(tool):
        signature = inspect.signature(tool)
        if "contact" not in signature.parameters:
            return tool
        @functools.wraps(tool)
        def scoped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            contact = str(bound.arguments.get("contact") or contact_scope)
            if contact != contact_scope:
                return _dump({"ok":False,"status":"blocked","reason":"contact_outside_current_request"})
            bound.arguments["contact"] = contact_scope
            return tool(*bound.args, **bound.kwargs)
        scoped.__signature__ = signature
        return scoped
    return [bind_contact(tool) for tool in tools]
