from __future__ import annotations

import hashlib
import json
import re
import time
import threading
from typing import Annotated, Any
from urllib.parse import quote

import requests

from meuharness.browser_control import _cdp
from meuharness.colorglass_quotation_tools import SITE, _active, _eval, _wait_ready, export_colorglass_quote_pdf

API_BASE = "https://colorglass.onrender.com"
_RUNTIME_URL = f"{SITE}/agent-portas-runtime.html"
_HTTP_SESSIONS: dict[str, requests.Session] = {}
_HTTP_LOCK = threading.RLock()


def _http_session(agent_id: str) -> requests.Session:
    with _HTTP_LOCK:
        session = _HTTP_SESSIONS.get(agent_id)
        if session is None:
            session = requests.Session()
            session.headers.update({"Accept": "application/json", "Connection": "keep-alive"})
            _HTTP_SESSIONS[agent_id] = session
        return session


def _token(agent_id: str) -> str:
    token = str(_eval(agent_id, "localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||''") or "")
    if not token:
        raise RuntimeError("auth_required")
    return token


def _api(agent_id: str, method: str, path: str, *, payload: dict[str, Any] | None = None, timeout: float = 10.0) -> tuple[int, Any]:
    token = _token(agent_id)
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = _http_session(agent_id).request(method, f"{API_BASE}{path}", headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return 0, {"success": False, "status": "network_error", "error": str(exc)}
    try:
        data = response.json()
    except ValueError:
        data = {"success": False, "status": "invalid_response", "error": response.text[:500]}
    return response.status_code, data


def _orders(agent_id: str) -> dict[str, Any]:
    status, data = _api(agent_id, "GET", "/api/orcamentos")
    if status in (401, 403):
        return {"ok": False, "status": "auth_required", "http_status": status}
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "status": "network_error", "http_status": status, "error": (data or {}).get("error") if isinstance(data, dict) else None}
    rows = data.get("orcamentos") if isinstance(data.get("orcamentos"), list) else []
    return {"ok": True, "orders": rows}


def _norm(value: Any) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").strip().lower()


def _resolve_order(agent_id: str, ref: str) -> dict[str, Any]:
    listed = _orders(agent_id)
    if not listed.get("ok"):
        return listed
    value = str(ref or "").strip()
    rows = listed["orders"]
    exact = [row for row in rows if str(row.get("id")) == value or str(row.get("numero_pedido")) == value]
    if len(exact) == 1:
        row = exact[0]
        return {"ok": True, "id": row.get("id"), "numero_pedido": row.get("numero_pedido"), "cliente": row.get("cliente_nome")}
    names = [row for row in rows if _norm(row.get("cliente_nome")) == _norm(value)]
    if len(names) == 1:
        row = names[0]
        return {"ok": True, "id": row.get("id"), "numero_pedido": row.get("numero_pedido"), "cliente": row.get("cliente_nome")}
    if len(names) > 1:
        return {"ok": False, "status": "ambiguous_order", "matches": [{"id": r.get("id"), "numero_pedido": r.get("numero_pedido"), "cliente": r.get("cliente_nome")} for r in names[:12]]}
    return {"ok": False, "status": "order_not_found", "ref": value}


def _ensure_core(agent_id: str, fallback_uuid: str | None = None) -> None:
    try:
        if _eval(agent_id, "typeof ColorGlassPortasAgent==='object' && typeof ColorGlassPortasAgent.preview==='function'"):
            return
    except Exception:
        pass

    _, _, ws = _active(agent_id)
    try:
        _cdp(ws, "Page.navigate", {"url": _RUNTIME_URL}, timeout_s=8)
        _wait_ready(agent_id)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _eval(agent_id, "typeof ColorGlassPortasAgent==='object' && typeof ColorGlassPortasAgent.preview==='function'"):
                return
            time.sleep(0.08)
    except Exception:
        pass

    if not fallback_uuid:
        listed = _orders(agent_id)
        rows = listed.get("orders") if listed.get("ok") else []
        fallback_uuid = str((rows or [{}])[0].get("id") or "")
    if not fallback_uuid:
        raise RuntimeError("quote_runtime_unavailable")
    _, _, ws = _active(agent_id)
    _cdp(ws, "Page.navigate", {"url": f"{SITE}/portas.html?orcamento_uuid={quote(fallback_uuid)}"}, timeout_s=8)
    _wait_ready(agent_id)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _eval(agent_id, "typeof ColorGlassPortasAgent==='object' && typeof ColorGlassPortasAgent.preview==='function'"):
            return
        time.sleep(0.1)
    raise RuntimeError("quote_runtime_not_ready")


def _preview(agent_id: str, spec: dict[str, Any], fallback_uuid: str | None = None) -> dict[str, Any]:
    _ensure_core(agent_id, fallback_uuid)
    expression = "(async()=>ColorGlassPortasAgent.preview(" + json.dumps(spec, ensure_ascii=False) + "))()"
    result = _eval(agent_id, expression, await_promise=True)
    return result if isinstance(result, dict) else {"ok": False, "status": "invalid_preview_response"}


def _request_id(run_id: str | None, order_uuid: str, spec: dict[str, Any]) -> str:
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    run = (run_id or f"manual-{time.time_ns()}").replace(" ", "-")
    return f"actis:{run}:{order_uuid}:{digest}"[:160]


def _door_data(door: dict[str, Any]) -> dict[str, Any]:
    data = door.get("dados")
    if isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and ":" in item:
                key, value = item.split(":", 1)
                out[key] = value
    return out


def _append_and_verify(agent_id: str, order: dict[str, Any], preview: dict[str, Any], request_id: str) -> dict[str, Any]:
    order_uuid = str(order["id"])
    door = dict(preview.get("porta") or {})
    data = dict(door.get("dados") or {})
    data["agent_request_id"] = request_id
    door["dados"] = data
    status, saved = _api(agent_id, "POST", f"/api/orcamento/{quote(order_uuid)}/portas/append", payload={"request_id": request_id, "porta": door}, timeout=15)
    if status in (401, 403):
        return {"ok": False, "status": "auth_required", "http_status": status}
    if status < 200 or status >= 300 or not isinstance(saved, dict) or saved.get("success") is False:
        return {"ok": False, "status": (saved or {}).get("status", "save_failed") if isinstance(saved, dict) else "save_failed", "http_status": status, "error": (saved or {}).get("error") if isinstance(saved, dict) else None, "order_uuid": order_uuid}
    match = saved.get("porta_salva") if isinstance(saved.get("porta_salva"), dict) else None
    if saved.get("status") != "verified" or not match or str(_door_data(match).get("agent_request_id") or "") != request_id:
        return {"ok": False, "status": "verify_failed", "mutation_may_have_occurred": True, "order_uuid": order_uuid, "request_id": request_id}
    resolved = preview.get("resolved") or {}
    return {
        "ok": True,
        "status": "verified",
        "id": order_uuid,
        "numero_pedido": saved.get("numero_pedido") or order.get("numero_pedido"),
        "cliente": order.get("cliente"),
        "quantidade_total": saved.get("quantidade_total"),
        "valor_total": float(saved.get("valor_total") or 0),
        "porta_preco": float(match.get("preco") or 0),
        "request_id": request_id,
        "idempotent_replay": bool(saved.get("idempotent_replay")),
        "tipo": match.get("tipo"),
        "perfil": (resolved.get("perfil") or {}).get("nome"),
        "vidro": (resolved.get("vidro") or {}).get("tipo"),
        "sistema": (resolved.get("sistema") or {}).get("nome") if isinstance(resolved.get("sistema"), dict) else None,
    }


def _normalize_glass_ref(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^espelho\b", "esp", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+(?:[.,]\d+)?)\s+mm\b", r"\1mm", text, flags=re.IGNORECASE)
    return text


def _door_spec(*, door_type: str, quantity: int, width_mm: int, height_mm: int, profile: str, glass: str,
               hinges: int, hinge_side: str, puller: str, puller_side: str, puller_size_mm: int,
               environment: str, additional_value: float, system: str, upper_track: str, lower_track: str,
               production_note: str) -> dict[str, Any]:
    return {
        "tipo": str(door_type or "giro"), "quantidade": int(quantity), "largura_mm": int(width_mm), "altura_mm": int(height_mm),
        "perfil": str(profile or ""), "vidro": _normalize_glass_ref(glass), "dobradicas": int(hinges), "hinge_side": str(hinge_side or "esquerda"),
        "puxador": str(puller or "sem_puxador"), "handle_side": str(puller_side or "direita"), "handle_size_mm": int(puller_size_mm or 0),
        "ambiente": str(environment or ""), "valor_adicional": float(additional_value or 0), "sistema": str(system or ""),
        "upper_track": str(upper_track or ""), "lower_track": str(lower_track or ""), "production_note": str(production_note or ""),
    }


QUOTE_SCHEMAS = {
    "giro": {
        "tipologia": "giro",
        "required": ["quantity", "width_mm", "height_mm", "profile_code", "color", "glass"],
        "optional": {
            "hinges": 2, "hinge_side": "esquerda", "puller": "sem_puxador",
            "puller_side": "direita", "puller_size_mm": 0, "environment": "",
            "additional_value": 0.0, "production_note": "",
        },
        "compose": {"profile": "<profile_code> <color>"},
    },
    "fixa": {
        "tipologia": "fixa",
        "required": ["quantity", "width_mm", "height_mm", "profile_code", "color", "glass"],
        "optional": {"environment": "", "additional_value": 0.0, "production_note": ""},
        "fixed": {"hinges": 0, "puller": "sem_puxador"},
        "compose": {"profile": "<profile_code> <color>"},
    },
    "deslizante": {
        "tipologia": "deslizante",
        "required": ["quantity", "width_mm", "height_mm", "profile_code", "color", "glass"],
        "optional": {
            "system": "", "upper_track": "", "lower_track": "", "environment": "",
            "additional_value": 0.0, "production_note": "",
        },
        "compose": {"profile": "<profile_code> <color>"},
        "note": "system/upper_track/lower_track só devem ser perguntados se o sistema escolhido ou o preview oficial exigir; não pergunte por padrão.",
    },
}


def build_colorglass_fast_tools(agent_id: str, *, run_id: str | None = None) -> list[object]:
    def colorglass_quote_schema(door_type: Annotated[str, "Tipologia: giro, fixa ou deslizante"]) -> str:
        """Retorne o contrato JSON mínimo da tipologia para o comercial saber exatamente o que precisa coletar."""
        key = str(door_type or "").strip().lower()
        aliases = {"correr": "deslizante", "deslizar": "deslizante", "fixo": "fixa"}
        key = aliases.get(key, key)
        schema = QUOTE_SCHEMAS.get(key)
        if not schema:
            return json.dumps({"ok": False, "status": "unknown_typology", "choices": sorted(QUOTE_SCHEMAS)}, ensure_ascii=False)
        return json.dumps({"ok": True, "schema": schema}, ensure_ascii=False)

    def colorglass_quote_create_with_door(
        customer_name: Annotated[str, "Nome do cliente"],
        door_type: Annotated[str, "Tipologia: giro, fixa ou deslizante"],
        quantity: Annotated[int, "Quantidade de portas"],
        width_mm: Annotated[int, "Largura em mm"],
        height_mm: Annotated[int, "Altura em mm"],
        profile: Annotated[str, "Perfil/acabamento, ex.: 1036 prata"],
        glass: Annotated[str, "Vidro/espelho, ex.: esp prata 4mm"],
        hinges: Annotated[int, "Dobradiças; use 0 para fixa"] = 2,
        hinge_side: Annotated[str, "Lado das dobradiças"] = "esquerda",
        puller: Annotated[str, "Puxador ou sem_puxador"] = "sem_puxador",
        puller_side: Annotated[str, "Lado do puxador"] = "direita",
        puller_size_mm: Annotated[int, "Medida do puxador em mm"] = 0,
        environment: Annotated[str, "Ambiente"] = "",
        additional_value: Annotated[float, "Valor adicional por porta"] = 0.0,
        system: Annotated[str, "Sistema para deslizante"] = "",
        upper_track: Annotated[str, "Trilho superior para deslizante"] = "",
        lower_track: Annotated[str, "Trilho inferior para deslizante"] = "",
        production_note: Annotated[str, "Observação de produção"] = "",
    ) -> str:
        """Crie ficha + porta em uma única operação determinística, salvando somente após preview válido e verificando no backend."""
        name = str(customer_name or "").strip()
        if not name:
            return json.dumps({"ok": False, "status": "invalid_customer"}, ensure_ascii=False)
        spec = _door_spec(door_type=door_type, quantity=quantity, width_mm=width_mm, height_mm=height_mm, profile=profile, glass=glass,
                          hinges=hinges, hinge_side=hinge_side, puller=puller, puller_side=puller_side, puller_size_mm=puller_size_mm,
                          environment=environment, additional_value=additional_value, system=system, upper_track=upper_track,
                          lower_track=lower_track, production_note=production_note)
        preview = _preview(agent_id, spec, None)
        if not preview.get("ok"):
            return json.dumps(preview, ensure_ascii=False, default=str)
        status, created = _api(agent_id, "POST", "/api/orcamento", payload={"cliente_nome": name})
        if status in (401, 403):
            return json.dumps({"ok": False, "status": "auth_required"}, ensure_ascii=False)
        if status < 200 or status >= 300 or not isinstance(created, dict) or created.get("success") is False:
            return json.dumps({"ok": False, "status": "create_failed", "http_status": status, "error": (created or {}).get("error") if isinstance(created, dict) else None}, ensure_ascii=False)
        order_uuid = str(created.get("id") or created.get("uuid") or "")
        if not order_uuid:
            return json.dumps({"ok": False, "status": "create_failed", "error": "Backend não retornou UUID"}, ensure_ascii=False)
        order = {"id": order_uuid, "numero_pedido": created.get("numero_pedido"), "cliente": name}
        request_id = _request_id(run_id, order_uuid, spec)
        result = _append_and_verify(agent_id, order, preview, request_id)
        if result.get("ok"):
            pdf = export_colorglass_quote_pdf(agent_id, str(result.get("numero_pedido") or order_uuid), run_id=run_id)
            result["pdf_ready"] = bool(pdf.get("ok"))
            result["pdf_status"] = pdf.get("status")
            result["pdf_filename"] = pdf.get("filename")
            result["pdf_path"] = pdf.get("path")
            result["artifact_id"] = pdf.get("artifact_id")
            if not pdf.get("ok"):
                result["pdf_error"] = pdf.get("error") or pdf.get("status")
        return json.dumps(result, ensure_ascii=False, default=str)

    def colorglass_quote_add_door_fast(
        order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"],
        door_type: Annotated[str, "Tipologia: giro, fixa ou deslizante"],
        quantity: Annotated[int, "Quantidade de portas"],
        width_mm: Annotated[int, "Largura em mm"],
        height_mm: Annotated[int, "Altura em mm"],
        profile: Annotated[str, "Perfil/acabamento"],
        glass: Annotated[str, "Vidro/espelho"],
        hinges: Annotated[int, "Dobradiças; use 0 para fixa"] = 2,
        hinge_side: Annotated[str, "Lado das dobradiças"] = "esquerda",
        puller: Annotated[str, "Puxador ou sem_puxador"] = "sem_puxador",
        puller_side: Annotated[str, "Lado do puxador"] = "direita",
        puller_size_mm: Annotated[int, "Medida do puxador em mm"] = 0,
        environment: Annotated[str, "Ambiente"] = "",
        additional_value: Annotated[float, "Valor adicional por porta"] = 0.0,
        system: Annotated[str, "Sistema para deslizante"] = "",
        upper_track: Annotated[str, "Trilho superior para deslizante"] = "",
        lower_track: Annotated[str, "Trilho inferior para deslizante"] = "",
        production_note: Annotated[str, "Observação de produção"] = "",
    ) -> str:
        """Adicione uma porta usando preview canônico aquecido + append idempotente + verificação independente."""
        _ensure_core(agent_id, None)
        order = _resolve_order(agent_id, order_ref)
        if not order.get("ok"):
            return json.dumps(order, ensure_ascii=False)
        spec = _door_spec(door_type=door_type, quantity=quantity, width_mm=width_mm, height_mm=height_mm, profile=profile, glass=glass,
                          hinges=hinges, hinge_side=hinge_side, puller=puller, puller_side=puller_side, puller_size_mm=puller_size_mm,
                          environment=environment, additional_value=additional_value, system=system, upper_track=upper_track,
                          lower_track=lower_track, production_note=production_note)
        preview = _preview(agent_id, spec, str(order["id"]))
        if not preview.get("ok"):
            return json.dumps(preview, ensure_ascii=False, default=str)
        request_id = _request_id(run_id, str(order["id"]), spec)
        result = _append_and_verify(agent_id, order, preview, request_id)
        if result.get("ok"):
            pdf = export_colorglass_quote_pdf(agent_id, str(result.get("numero_pedido") or order["id"]), run_id=run_id)
            result["pdf_ready"] = bool(pdf.get("ok"))
            result["pdf_status"] = pdf.get("status")
            result["pdf_filename"] = pdf.get("filename")
            result["pdf_path"] = pdf.get("path")
            result["artifact_id"] = pdf.get("artifact_id")
            if not pdf.get("ok"):
                result["pdf_error"] = pdf.get("error") or pdf.get("status")
        return json.dumps(result, ensure_ascii=False, default=str)

    return [colorglass_quote_schema, colorglass_quote_create_with_door, colorglass_quote_add_door_fast]
