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
ALTERATION_RE = re.compile(r"\b(?:alter|ajust|corrig|mud|troc|troqu|substitu|remov|adicion|inclu|acrescent)[a-z]*\b", re.I)
NEW_QUOTE_RE = re.compile(r"\b(?:novo|nova|outro|outra)\s+(?:orcamento|cotacao|pedido)\b", re.I)
ORDER_REF_RE = re.compile(r"\b(?:pedido|orcamento)\s*#?\s*(\d+)\b", re.I)
TECHNICAL_PHASE_RE = re.compile(
    r"\b(?:aprovad[oa]|fechad[oa]|vamos\s+fechar|quero\s+fechar|pode\s+(?:produzir|fazer\s+(?:o\s+)?pedido|fazer\s+(?:as\s+)?portas?)|"
    r"(?:manda|mandar|passa|passar|envia|enviar).{0,18}produ[cç][aã]o|"
    r"ficha\s+t[eé]cnica|dados?\s+t[eé]cnicos?|medidas?\s+finais?|ordem\s+de\s+produ[cç][aã]o)\b",
    re.I,
)


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
    color_space = norm
    for glass_phrase in ("espelho prata", "esp prata", "reflecta bronze", "ref bronze", "reflecta prata", "ref prata"):
        color_space = re.sub(rf"\b{re.escape(glass_phrase)}\b", " ", color_space)
    color = next((c for c in COLORS if re.search(rf"\b{re.escape(_norm(c))}\b", color_space)), None)
    if color:
        fields["color"] = _norm(color)
    if code:
        fields["profile_code"] = code
        fields["profile"] = " ".join(x for x in (code, _norm(color) if color else "") if x)
    if re.search(r"\bdeslizante\b|\bcorrer\b", norm):
        fields["typology"] = "deslizante"
    elif re.search(r"\bfixa\b", norm):
        fields["typology"] = "fixa"
    elif re.search(r"\bgiro\b", norm):
        fields["typology"] = "giro"
    if re.search(r"\besp(?:elho)?\s*\.?\s*prata\b", norm):
        fields["glass"] = "esp prata"
    elif "reflecta bronze" in norm or "ref bronze" in norm:
        fields["glass"] = "reflecta bronze"
    elif "acidato incolor" in norm:
        fields["glass"] = "acidato incolor"
    explicit_h = (
        re.search(r"(\d{2,4})\s*(?:mm\s*)?de\s*altura", norm)
        or re.search(r"(?:altura|alt)\s*[:=]?\s*(\d{2,4})(?:\s*mm)?\b", norm)
    )
    explicit_w = (
        re.search(r"(\d{2,4})\s*(?:mm\s*)?de\s*largura", norm)
        or re.search(r"(?:largura|larg)\s*[:=]?\s*(\d{2,4})(?:\s*mm)?\b", norm)
    )
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
        hinge_side = re.search(r"dobradi[cç]as?.{0,25}\b(esquerda|direita)\b|\b(esquerda|direita)\b.{0,25}dobradi[cç]as?", raw, re.I)
        if hinge_side:
            fields["hinge_side"] = _norm(hinge_side.group(1) or hinge_side.group(2))
    if re.search(r"\bsem\s+puxador\b", norm):
        fields["puller"] = "sem_puxador"
    puller_side = re.search(r"puxador.{0,30}\b(esquerda|direita)\b|\b(esquerda|direita)\b.{0,30}puxador", raw, re.I)
    if puller_side:
        fields["puller_side"] = _norm(puller_side.group(1) or puller_side.group(2))
    puller_size = re.search(r"(?:medida|tamanho)\s+(?:do\s+)?puxador\s*[:=]?\s*(\d{2,4})|puxador.{0,20}?(\d{2,4})\s*mm", norm, re.I)
    if puller_size:
        fields["puller_size_mm"] = int(puller_size.group(1) or puller_size.group(2))
    for key, pattern in (
        ("environment", r"(?:ambiente|comodo)\s*[:=]\s*([^,;.]+)"),
        ("drilling_position", r"(?:furacao|furacoes|furação|furações)\s*[:=]\s*([^,;.]+)"),
        ("system", r"sistema\s*[:=]\s*([^,;.]+)"),
        ("upper_track", r"trilho\s+superior\s*[:=]\s*([^,;.]+)"),
        ("lower_track", r"trilho\s+inferior\s*[:=]\s*([^,;.]+)"),
    ):
        match = re.search(pattern, raw, re.I)
        if match:
            fields[key] = match.group(1).strip()
    upper_gap = re.search(r"v[aã]o\s+(?:do\s+)?trilho\s+superior\s*[:=]?\s*(\d{1,4})", raw, re.I)
    lower_gap = re.search(r"v[aã]o\s+(?:do\s+)?trilho\s+inferior\s*[:=]?\s*(\d{1,4})", raw, re.I)
    if upper_gap:
        fields["upper_track_gap_mm"] = int(upper_gap.group(1))
    if lower_gap:
        fields["lower_track_gap_mm"] = int(lower_gap.group(1))
    return fields


def looks_like_quote(text: str) -> bool:
    norm = _norm(text)
    return any(token in norm for token in ("orca", "orcamento", "porta", "1036", "perfil", "espelho", "vidro", "dobradic"))


def extract_order_ref(text: str) -> str:
    match = ORDER_REF_RE.search(_norm(text))
    return match.group(1) if match else ""


def detect_quote_intent(text: str, prior: dict[str, Any] | None = None) -> dict[str, Any]:
    prior = dict(prior or {})
    norm = _norm(text)
    order_ref = extract_order_ref(text) or str(prior.get("order_ref") or "")
    alteration = bool(ALTERATION_RE.search(norm))
    explicit_new = bool(NEW_QUOTE_RE.search(norm))
    quote_request = bool(re.search(r"\b(?:orca|orcar|orcamento|cotacao)\b", norm))
    prior_status = str(prior.get("status") or "idle")
    if alteration:
        mode = "alteration"
    elif explicit_new or prior_status == "idle" or (prior_status in {"quoted", "completed"} and quote_request):
        mode = "new"
    else:
        mode = str(prior.get("quote_mode") or "new")
    return {"mode": mode, "order_ref": order_ref, "explicit_new": explicit_new, "alteration": alteration}


def looks_like_technical_phase_transition(text: str) -> bool:
    return bool(TECHNICAL_PHASE_RE.search(str(text or "")))


def detect_quote_phase(text: str, prior: dict[str, Any] | None = None) -> str:
    """Return the business phase without confusing price collection with production data."""
    prior = dict(prior or {})
    intent = detect_quote_intent(text, prior)
    if intent.get("explicit_new"):
        return "commercial"
    if looks_like_technical_phase_transition(text):
        return "technical"
    phase = str(prior.get("quote_phase") or "commercial").strip().lower()
    return phase if phase in {"commercial", "technical"} else "commercial"


def commercial_missing_fields(quote: dict[str, Any]) -> list[str]:
    if str(quote.get("quote_mode") or "new") == "alteration":
        return [] if str(quote.get("order_ref") or "").strip() else ["número do pedido/orçamento"]
    fields = dict(quote.get("fields") or {})
    missing: list[str] = []
    if not fields.get("quantity"):
        missing.append("quantidade")
    if not fields.get("typology"):
        missing.append("tipo (giro/fixa/deslizante)")
    profile = str(fields.get("profile") or fields.get("profile_code") or "").strip()
    color = str(fields.get("color") or "").strip()
    if not profile:
        missing.append("perfil")
    if not color and not any(_norm(c) in _norm(profile) for c in COLORS):
        missing.append("cor")
    if not fields.get("glass"):
        missing.append("vidro")
    if not fields.get("height_mm") or not fields.get("width_mm"):
        missing.append("medida (A×L)")
    return missing


def technical_missing_fields(quote: dict[str, Any]) -> list[str]:
    """Production-only data. Commercial defaults are never treated as technical confirmation."""
    fields = dict(quote.get("fields") or {})
    typology = str(fields.get("typology") or "").strip().lower()
    missing: list[str] = []
    if not fields.get("height_mm") or not fields.get("width_mm"):
        missing.append("medidas finais (A×L)")
    if not fields.get("environment"):
        missing.append("ambiente")
    if typology == "giro":
        if fields.get("hinges") is None:
            missing.append("quantidade de dobradiças")
        if not fields.get("hinge_side"):
            missing.append("lado das dobradiças")
        if not fields.get("hinge_heights_mm"):
            missing.append("alturas das dobradiças")
        if not fields.get("drilling_position"):
            missing.append("posição das furações")
    if typology == "deslizante":
        if not fields.get("system"):
            missing.append("sistema")
        if not fields.get("upper_track"):
            missing.append("trilho superior")
        if not fields.get("lower_track"):
            missing.append("trilho inferior")
        if fields.get("upper_track_gap_mm") is None:
            missing.append("vão do trilho superior")
        if fields.get("lower_track_gap_mm") is None:
            missing.append("vão do trilho inferior")
    puller = str(fields.get("puller") or "").strip().lower()
    if not puller:
        missing.append("puxador/sem puxador")
    elif puller not in {"sem_puxador", "sem puxador", "nenhum"}:
        if not fields.get("puller_side"):
            missing.append("posição do puxador")
        if fields.get("puller_size_mm") is None:
            missing.append("medida do puxador")
    return missing


def quote_missing_fields(quote: dict[str, Any]) -> list[str]:
    if str(quote.get("quote_phase") or "commercial") == "technical":
        return technical_missing_fields(quote)
    return commercial_missing_fields(quote)


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
        return {"fields": {}, "status": "idle", "pending_confirmation": None, "revision": 0,
                "quote_phase": "commercial", "quote_mode": "new", "order_ref": ""}
    _, payload = _load_payload(conversation["id"])
    quote = dict(payload.get("quote_state") or {})
    quote.setdefault("fields", {})
    quote.setdefault("status", "idle")
    quote.setdefault("pending_confirmation", None)
    quote.setdefault("revision", 0)
    quote.setdefault("quote_mode", "new")
    quote.setdefault("order_ref", "")
    quote.setdefault("quote_phase", "commercial")
    quote.setdefault("missing_fields", quote_missing_fields(quote))
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
    intent = detect_quote_intent(text, quote)
    prior_ref = str(quote.get("order_ref") or "")
    phase = detect_quote_phase(text, quote)
    starts_new = bool(
        not pending and intent["mode"] == "new"
        and (
            intent["explicit_new"]
            or (str(quote.get("status") or "") in {"quoted", "completed"} and looks_like_quote(text))
        )
    )
    switched_target = bool(
        intent["mode"] == "alteration" and intent["order_ref"]
        and prior_ref and intent["order_ref"] != prior_ref
    )
    if starts_new:
        phase = "commercial"
    fields = {} if starts_new or switched_target else dict(quote.get("fields") or {})
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
    profile_code = str(fields.get("profile_code") or "").strip()
    profile_text = str(fields.get("profile") or "").strip()
    if not profile_code:
        profile_code = next((code for code in PROFILE_CODES if re.search(rf"\b{re.escape(code)}\b", profile_text)), "")
        if profile_code:
            fields["profile_code"] = profile_code
    color = str(fields.get("color") or "").strip()
    if not color:
        color = next((_norm(c) for c in COLORS if re.search(rf"\b{re.escape(_norm(c))}\b", _norm(profile_text))), "")
        if color:
            fields["color"] = color
    if profile_code:
        fields["profile"] = " ".join(x for x in (profile_code, color) if x)

    next_status = (
        "technical_collecting" if phase == "technical"
        else ("collecting" if (looks_like_quote(text) or quote.get("status") != "idle") else "idle")
    )
    quote.update(
        fields=fields, pending_confirmation=pending, last_customer_text=str(text or ""),
        status=next_status, quote_phase=phase,
        quote_mode=str(intent["mode"]), order_ref=str(intent["order_ref"]),
        revision=int(quote.get("revision") or 0) + 1, updated_at=_now(),
    )
    quote["missing_fields"] = quote_missing_fields(quote)
    if phase == "technical" and not quote["missing_fields"]:
        quote["status"] = "technical_ready"
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
    order = re.search(r"(?:Número do Pedido|Pedido)[:#\s*]*(\d+)", str(result_text or ""), re.I)
    if order:
        quote["order_ref"] = order.group(1)
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
        "quote_mode": str(quote.get("quote_mode") or "new"),
        "quote_phase": str(quote.get("quote_phase") or "commercial"),
        "order_ref": str(quote.get("order_ref") or ""),
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
