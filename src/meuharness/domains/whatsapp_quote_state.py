from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from meuharness.storage import connect

AFFIRMATIVE = {"sim", "isso", "correto", "certo", "exato", "exatamente", "confirmo", "pode", "pode sim", "e isso", "é isso"}
NUMBER_WORDS = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "três": 3, "quatro": 4}
PROFILE_CODES = ("1036", "070", "066", "3446", "3545", "9700")
COLORS = ("prata", "dourado", "dourada", "preto", "preta", "bronze", "inox")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw.lower()).split())


def is_affirmative(text: str) -> bool:
    return _norm(text) in {_norm(x) for x in AFFIRMATIVE}


def _word_number(value: str) -> int | None:
    value = _norm(value)
    if value.isdigit():
        return int(value)
    return NUMBER_WORDS.get(value)


def extract_quote_fields(text: str) -> dict[str, Any]:
    """Extract only explicit facts. ColorGlass measure convention is ALTURA x LARGURA."""
    raw, norm = str(text or ""), _norm(text)
    fields: dict[str, Any] = {}
    qty = re.search(r"\b(\d+|uma|um|duas|dois|tres|três|quatro)\s+portas?\b", raw, re.I)
    if qty:
        value = _word_number(qty.group(1))
        if value:
            fields["quantity"] = value
    code = next((code for code in PROFILE_CODES if re.search(rf"\b{re.escape(code)}\b", norm)), None)
    color = next((c for c in COLORS if re.search(rf"\b{re.escape(_norm(c))}\b", norm)), None)
    if code:
        fields["profile"] = " ".join(x for x in (code, color) if x)
    if re.search(r"\besp(?:elho)?\s*\.?\s*prata\b", norm):
        fields["glass"] = "esp prata"
    elif "reflecta bronze" in norm or "ref bronze" in norm:
        fields["glass"] = "reflecta bronze"
    elif "acidato incolor" in norm:
        fields["glass"] = "acidato incolor"
    explicit_h = re.search(r"(\d{2,4})\s*(?:mm\s*)?de\s*altura", norm)
    explicit_w = re.search(r"(\d{2,4})\s*(?:mm\s*)?de\s*largura", norm)
    if explicit_h:
        fields["height_mm"] = int(explicit_h.group(1))
    if explicit_w:
        fields["width_mm"] = int(explicit_w.group(1))
    dims = re.search(r"\b(\d{2,4})\s*[x×]\s*(\d{2,4})\b", raw, re.I)
    if dims and "height_mm" not in fields and "width_mm" not in fields:
        fields["height_mm"], fields["width_mm"] = int(dims.group(1)), int(dims.group(2))
    hinges = re.search(r"\b(\d+|uma|um|duas|dois|tres|três|quatro)\s+dobradi[cç]as?\b", raw, re.I)
    if hinges:
        value = _word_number(hinges.group(1))
        if value:
            fields["hinges"] = value
            fields["typology"] = "giro"
    if "dobradic" in norm:
        nums = [int(x) for x in re.findall(r"\b\d{2,4}\b", norm)]
        dims_values = {fields.get("height_mm"), fields.get("width_mm")}
        positions = [n for n in nums if n not in dims_values]
        if len(positions) >= 2:
            fields["hinge_heights_mm"] = positions[-4:]
    return fields


def looks_like_quote(text: str) -> bool:
    norm = _norm(text)
    return any(token in norm for token in ("orca", "orcamento", "porta", "1036", "perfil", "espelho", "vidro", "dobradic"))


def _load_payload(conversation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM whatsapp_conversation_state WHERE conversation_id=?", (conversation_id,)).fetchone()
    state = dict(row) if row else {}
    try:
        payload = json.loads(state.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return state, payload


def get_quote_state(agent_id: str, contact_name: str) -> dict[str, Any]:
    from meuharness.domains.whatsapp_shadow import get_conversation_by_contact
    conversation = get_conversation_by_contact(agent_id, contact_name)
    if not conversation:
        return {"fields": {}, "status": "idle", "pending_confirmation": None, "revision": 0}
    _, payload = _load_payload(conversation["id"])
    quote = dict(payload.get("quote_state") or {})
    quote.setdefault("fields", {})
    quote.setdefault("status", "idle")
    quote.setdefault("pending_confirmation", None)
    quote.setdefault("revision", 0)
    return quote


def save_quote_state(agent_id: str, contact_name: str, quote: dict[str, Any]) -> dict[str, Any]:
    from meuharness.domains.whatsapp_shadow import ensure_conversation, get_contact, upsert_contact
    contact = get_contact(agent_id, contact_name) or upsert_contact(agent_id, contact_name)
    conversation = ensure_conversation(agent_id, contact["id"], contact_name)
    state, payload = _load_payload(conversation["id"])
    payload["quote_state"] = quote
    now = _now()
    with connect() as conn:
        conn.execute("""INSERT INTO whatsapp_conversation_state
            (conversation_id,current_subject,pending_items,last_customer_request,last_agent_action,payload,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET
            current_subject=excluded.current_subject,pending_items=excluded.pending_items,
            last_customer_request=excluded.last_customer_request,last_agent_action=excluded.last_agent_action,
            payload=excluded.payload,updated_at=excluded.updated_at""",
            (conversation["id"], "orcamento" if quote.get("status") != "idle" else state.get("current_subject", ""),
             json.dumps([quote.get("pending_confirmation", {}).get("question")] if quote.get("pending_confirmation") else [], ensure_ascii=False),
             str(quote.get("last_customer_text") or "")[:4000], str(quote.get("last_agent_action") or "")[:2000],
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now))
    return quote


def update_quote_state(agent_id: str, contact_name: str, text: str, *, evidence_ids: list[str] | None = None) -> dict[str, Any]:
    quote = get_quote_state(agent_id, contact_name)
    norm = _norm(text)
    pending = quote.get("pending_confirmation")
    starts_new = bool(re.search(r"\b(orca|orcar|orcamento|cotacao)\b", norm)) and not pending
    fields = {} if starts_new else dict(quote.get("fields") or {})
    if pending and is_affirmative(text) and isinstance(pending.get("proposed"), dict) and pending.get("proposed"):
        fields.update(pending["proposed"])
        pending = None
    elif pending:
        question = _norm(str(pending.get("question") or ""))
        if "lado" in question and norm in {"esquerda", "direita"}:
            fields["hinge_side"] = norm
            pending = None
        elif "puxador" in question and text.strip():
            fields["puller"] = text.strip()
            pending = None
        elif "ambiente" in question and text.strip():
            fields["environment"] = text.strip()
            pending = None
        else:
            fields.update(extract_quote_fields(text))
    else:
        fields.update(extract_quote_fields(text))
    quote.update(fields=fields, pending_confirmation=pending, last_customer_text=str(text or ""),
                 status="collecting" if (looks_like_quote(text) or quote.get("status") != "idle") else "idle",
                 revision=int(quote.get("revision") or 0) + 1, updated_at=_now())
    ids = list(dict.fromkeys([*list(quote.get("evidence_ids") or []), *list(evidence_ids or [])]))
    quote["evidence_ids"] = ids[-20:]
    return save_quote_state(agent_id, contact_name, quote)


def quote_fingerprint(quote: dict[str, Any]) -> str:
    body = json.dumps(quote.get("fields") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def set_quote_pending(agent_id: str, contact_name: str, question: str,
                      proposed: dict[str, Any] | None = None) -> dict[str, Any]:
    quote = get_quote_state(agent_id, contact_name)
    quote["pending_confirmation"] = {"question": str(question).strip(), "proposed": dict(proposed or {})}
    quote["status"] = "needs_information"
    quote["last_agent_action"] = "asked_confirmation"
    quote["updated_at"] = _now()
    return save_quote_state(agent_id, contact_name, quote)


def mark_quote_result(agent_id: str, contact_name: str, *, task_id: str,
                      status: str, result_text: str = "") -> dict[str, Any]:
    quote = get_quote_state(agent_id, contact_name)
    quote["company_task_id"] = task_id
    quote["company_task_status"] = status
    quote["company_task_result"] = str(result_text or "")[:6000]
    quote["last_dispatched_fingerprint"] = quote_fingerprint(quote)
    quote["status"] = "quoted" if status == "completed" else status
    quote["last_agent_action"] = "quote_result:" + status
    quote["updated_at"] = _now()
    return save_quote_state(agent_id, contact_name, quote)


def confirmation_question_from_result(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^(?:ACTIS_)?NEEDS_CONFIRMATION:\s*", "", value, flags=re.I)
    return value.strip()


def quote_runtime_payload(quote: dict[str, Any], *, contact_name: str, latest_text: str) -> dict[str, Any]:
    return {
        "contact": contact_name,
        "fields": dict(quote.get("fields") or {}),
        "operator_answer": latest_text,
        "pending_confirmation": quote.get("pending_confirmation"),
        "evidence_ids": list(quote.get("evidence_ids") or []),
        "state_revision": int(quote.get("revision") or 0),
    }


def format_quote_reply(result_text: str) -> str:
    """Turn a verbose specialist result into a short operator-facing WhatsApp reply."""
    text = str(result_text or "").strip()
    order = re.search(r"(?:Número do Pedido|Pedido)[:#\s*]*([0-9]+)", text, re.I)
    value = re.search(r"(?:Valor Total|Total)[:\s*]*R\$\s*([0-9.]+,[0-9]{2})", text, re.I)
    qty = re.search(r"Quantidade Total[:\s*]*([0-9]+)\s*portas?", text, re.I)
    profile = re.search(r"Perfil[:\s*]*([^\n]+)", text, re.I)
    glass = re.search(r"Vidro[:\s*]*([^\n]+)", text, re.I)
    height = re.search(r"Altura[:\s*]*([0-9]+)\s*mm", text, re.I)
    width = re.search(r"Largura[:\s*]*([0-9]+)\s*mm", text, re.I)
    parts: list[str] = []
    if order:
        parts.append(f"pedido #{order.group(1)}")
    if qty:
        parts.append(f"{qty.group(1)} portas")
    if profile:
        parts.append(profile.group(1).strip().strip("*"))
    if glass:
        parts.append(glass.group(1).strip().strip("*"))
    if height and width:
        parts.append(f"{height.group(1)}×{width.group(1)} mm")
    prefix = "Orçamento pronto"
    if parts:
        prefix += ": " + " — ".join(parts)
    if value:
        prefix += f". Total: R$ {value.group(1)}."
    elif text:
        prefix += ". " + text[:500]
    return prefix
