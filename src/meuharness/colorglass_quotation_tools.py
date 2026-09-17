from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from meuharness.browser_control import _cdp
from meuharness.native_browser_tools import _active, _value
from meuharness import storage

SITE = "https://www.colorglassfortaleza.com.br"


def _wait_ready(agent_id: str, timeout_s: float = 12.0) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        _, _, ws = _active(agent_id)
        try:
            last = str(_value(ws, "document.readyState") or "")
            if last == "complete":
                return ws
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"ColorGlass não concluiu o carregamento (readyState={last or 'desconhecido'}).")


def _eval(agent_id: str, expression: str, *, await_promise: bool = False) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _, _, ws = _active(agent_id)
            return _value(ws, expression, await_promise=await_promise)
        except (RuntimeError, TimeoutError) as exc:
            last_error = exc
            message = str(exc).lower()
            transient = any(token in message for token in (
                "navigated", "closed", "target", "timed out", "websocket",
            ))
            if not transient or attempt == 2:
                raise
            time.sleep(0.2 * (attempt + 1))
    raise last_error or RuntimeError("Falha transitória ao acessar a aba ColorGlass.")



def export_colorglass_quote_pdf(agent_id: str, order_ref: str) -> dict[str, Any]:
    """Export the official customer quote layout from Informações do orçamento as a local PDF."""
    ref = str(order_ref or "").strip()
    if not ref:
        raise ValueError("Referência do orçamento vazia.")
    resolve = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const h={'Authorization':'Bearer '+token};const list=await fetch(api+'/api/orcamentos',{headers:h}).then(r=>r.json()).catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').trim().toLowerCase();const o=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));return o?{ok:true,id:o.id,numero_pedido:o.numero_pedido,cliente:o.cliente_nome,quantidade_total:o.quantidade_total,valor_total:Number(o.valor_total||0)}:{ok:false,status:'order_not_found'};})()""" % json.dumps(ref)
    resolved = _eval(agent_id, resolve, await_promise=True)
    if not isinstance(resolved, dict) or not resolved.get("ok"):
        return dict(resolved or {"ok": False, "status": "order_not_found"})
    order_uuid = str(resolved["id"])
    _, _, ws = _active(agent_id)
    _cdp(ws, "Page.navigate", {"url": f"{SITE}/vizualizacao.html?orcamento_uuid={quote(order_uuid)}"}, timeout_s=8)
    deadline = time.monotonic() + 16
    while time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            ready = _value(ws, "document.readyState==='complete' && typeof imprimirOrcamentoCliente==='function' && !!ORCAMENTO_ATUAL && PORTAS_ATUAIS.length>0")
            if ready:
                break
        except Exception:
            pass
    else:
        return {"ok": False, "status": "pdf_page_not_ready", **resolved}
    prepared = _value(ws, "(()=>{const old=window.print;window.print=()=>{};try{imprimirOrcamentoCliente()}finally{window.print=old}const el=document.getElementById('printOrcamentoCliente');return !!el&&el.classList.contains('active')&&!!el.innerText.trim()})()")
    if not prepared:
        return {"ok": False, "status": "pdf_layout_not_ready", **resolved}
    _cdp(ws, "Emulation.setEmulatedMedia", {"media": "print"})
    pdf = _cdp(ws, "Page.printToPDF", {
        "printBackground": True, "preferCSSPageSize": True,
        "paperWidth": 8.27, "paperHeight": 11.69,
        "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
    }, timeout_s=18)
    raw = base64.b64decode(str(pdf.get("data") or ""))
    if not raw.startswith(b"%PDF-") or len(raw) < 1000:
        return {"ok": False, "status": "invalid_pdf", **resolved}
    number = re.sub(r"[^0-9A-Za-z_-]+", "-", str(resolved.get("numero_pedido") or ref)).strip("-")
    filename = f"Orcamento-ColorGlass-{number}.pdf"
    folder = storage.DATA_DIR / "artifacts" / "quotes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(raw)
    return {**resolved, "ok": True, "status": "pdf_ready", "path": str(path), "filename": filename,
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

def build_colorglass_quotation_tools(agent_id: str) -> list[object]:
    def colorglass_quote_create(customer_name: Annotated[str, "Nome do cliente para a nova ficha de orçamento"]) -> str:
        """Crie uma ficha real de orçamento ColorGlass e devolva UUID/pedido verificados."""
        name = str(customer_name or "").strip()
        if not name:
            raise ValueError("Nome do cliente vazio.")
        expr = """(async()=>{const name=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const storeID=localStorage.getItem('USER_STOREID')||undefined;const res=await fetch(api+'/api/orcamento',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({cliente_nome:name,storeID})});const data=await res.json().catch(()=>({}));if(!res.ok||data.success===false)return {ok:false,status:'create_failed',http:res.status,error:data.error||'falha ao criar ficha'};const list=await fetch(api+'/api/orcamentos',{headers:{'Authorization':'Bearer '+token}}).then(r=>r.json()).catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const item=rows.find(x=>String(x.id)===String(data.id));return {ok:true,id:data.id,numero_pedido:item?.numero_pedido??data.numero_pedido??null,cliente:item?.cliente_nome||name,quantidade_total:item?.quantidade_total??0,valor_total:Number(item?.valor_total||0)};})()""" % json.dumps(name)
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    def colorglass_quote_add_door(
        order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"],
        quantity: Annotated[int, "Quantidade de portas"],
        width_mm: Annotated[int, "Largura em milímetros"],
        height_mm: Annotated[int, "Altura em milímetros"],
        profile: Annotated[str, "Perfil/acabamento, ex.: 1036 bronze"],
        glass: Annotated[str, "Vidro/espelho, ex.: esp prata"],
        hinges: Annotated[int, "Quantidade de dobradiças"] = 3,
        hinge_side: Annotated[str, "Lado das dobradiças: esquerda ou direita"] = "esquerda",
        puller: Annotated[str, "Puxador; use sem_puxador quando não houver"] = "sem_puxador",
        environment: Annotated[str, "Ambiente opcional"] = "",
        replace_existing: Annotated[bool, "Substituir as portas existentes do orçamento"] = False,
    ) -> str:
        """Salve uma porta de giro no orçamento real e releia total/quantidade no backend."""
        ref = str(order_ref or "").strip()
        if not ref:
            raise ValueError("Referência do orçamento vazia.")
        if int(quantity) < 1 or int(width_mm) < 1 or int(height_mm) < 1:
            raise ValueError("Quantidade e medidas devem ser positivas.")
        side = str(hinge_side or "").strip().lower()
        if side not in {"esquerda", "direita"}:
            raise ValueError("hinge_side deve ser esquerda ou direita.")

        resolve = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const list=await fetch(api+'/api/orcamentos',{headers:{'Authorization':'Bearer '+token}}).then(r=>r.json()).catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').trim().toLowerCase();const item=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));return item?{ok:true,id:item.id,numero_pedido:item.numero_pedido,cliente:item.cliente_nome}:{ok:false,status:'order_not_found',ref};})()""" % json.dumps(ref)
        resolved = _eval(agent_id, resolve, await_promise=True)
        if not isinstance(resolved, dict) or not resolved.get("ok"):
            return json.dumps(resolved or {"ok": False, "status": "order_not_found"}, ensure_ascii=False)
        order_uuid = str(resolved["id"])

        _, _, ws = _active(agent_id)
        _cdp(ws, "Page.navigate", {"url": f"{SITE}/portas.html?orcamento_uuid={quote(order_uuid)}"})
        _wait_ready(agent_id)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if _eval(agent_id, "typeof salvarPorta==='function' && !!document.querySelector('#tipologia')"):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Tela de portas não carregou funções básicas a tempo.")

        cfg = {
            "quantity": int(quantity), "width": int(width_mm), "height": int(height_mm),
            "profile": str(profile), "glass": str(glass), "hinges": int(hinges),
            "side": side, "puller": str(puller or "sem_puxador"),
            "environment": str(environment or ""), "replace": bool(replace_existing),
        }
        expr = """(async()=>{const cfg=%s;const norm=s=>String(s??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();const pick=(sel,q)=>{const e=document.querySelector(sel);if(!e)return {ok:false,error:'missing '+sel};const toks=norm(q).split(/\\s+/).filter(Boolean);const opts=[...e.options].filter(o=>o.value);const match=opts.find(o=>norm(o.text)===norm(q))||opts.find(o=>toks.every(t=>norm(o.text).includes(t)));if(!match)return {ok:false,error:'option_not_found',selector:sel,query:q,choices:opts.slice(0,30).map(o=>o.text)};e.value=match.value;e.dispatchEvent(new Event('change',{bubbles:true}));return {ok:true,text:match.text,value:match.value};};const set=(sel,v)=>{const e=document.querySelector(sel);if(!e)return false;e.value=String(v);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true;};const tipo=document.querySelector('#tipologia');tipo.value='giro';tipo.dispatchEvent(new Event('change',{bubbles:true}));for(let i=0;i<40 && (document.querySelectorAll('#perfil option').length<2||document.querySelectorAll('#vidro option').length<2);i++)await new Promise(r=>setTimeout(r,100));if(document.querySelectorAll('#perfil option').length<2||document.querySelectorAll('#vidro option').length<2)return {ok:false,status:'catalog_not_ready'};set('#quantidade',cfg.quantity);set('#largura',cfg.width);set('#altura',cfg.height);const p=pick('#perfil',cfg.profile);const g=pick('#vidro',cfg.glass);if(!p.ok||!g.ok)return {ok:false,status:'catalog_match_failed',profile:p,glass:g};set('#dobradicas',cfg.hinges);set('#dobradicas_posicao',cfg.side);await new Promise(r=>setTimeout(r,80));const pux=document.querySelector('#puxador');if(!pux)return {ok:false,status:'missing_puller'};const requested=norm(cfg.puller);const po=[...pux.options].find(o=>o.value===cfg.puller)||[...pux.options].find(o=>norm(o.text).includes(requested.includes('sem')?'sem puxador':requested));if(!po)return {ok:false,status:'puller_not_found',choices:[...pux.options].map(o=>o.text)};pux.value=po.value;pux.dispatchEvent(new Event('change',{bubbles:true}));set('#ambiente',cfg.environment||'');if(cfg.replace){portas=[];editando=null;}window.__actisQuoteAlerts=[];window.alert=m=>window.__actisQuoteAlerts.push(String(m));const total=typeof calcularPrecoPorta==='function'?Number(calcularPrecoPorta()||0):0;const saveOriginal=window.salvarPortasBackend;window.salvarPortasBackend=async rows=>saveOriginal(rows.map(row=>{const dados={...(row.dados||{})};if(Array.isArray(dados.dobradicas_alturas))dados.dobradicas_alturas=dados.dobradicas_alturas.join(',');return {...row,dados};}));try{await salvarPorta();}finally{window.salvarPortasBackend=saveOriginal;}await new Promise(r=>setTimeout(r,150));const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';const h={'Authorization':'Bearer '+token};const saved=await fetch(api+'/api/orcamento/'+encodeURIComponent(ORCAMENTO_UUID)+'/portas',{headers:h}).then(r=>r.json());const list=await fetch(api+'/api/orcamentos',{headers:h}).then(r=>r.json());const order=(Array.isArray(list?.orcamentos)?list.orcamentos:[]).find(x=>String(x.id)===String(ORCAMENTO_UUID));return {ok:saved?.success===true,status:saved?.success===true?'saved':'verify_failed',numero_pedido:order?.numero_pedido,cliente:order?.cliente_nome,quantidade_total:order?.quantidade_total,valor_total:Number(order?.valor_total||0),calculated_total:Number(total.toFixed(2)),profile:p.text,glass:g.text,alerts:window.__actisQuoteAlerts,portas:saved?.portas||[]};})()""" % json.dumps(cfg, ensure_ascii=False)
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    def colorglass_quote_pdf(order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"]) -> str:
        """Exporte o PDF oficial 'Orçamento do cliente' da tela Informações do orçamento."""
        return json.dumps(export_colorglass_quote_pdf(agent_id, order_ref), ensure_ascii=False, default=str)

    def colorglass_quote_verify(order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"]) -> str:
        """Verifique orçamento e portas diretamente no backend oficial pela sessão autenticada."""
        ref = str(order_ref or "").strip()
        expr = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const h={'Authorization':'Bearer '+token};const list=await fetch(api+'/api/orcamentos',{headers:h}).then(r=>r.json());const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').trim().toLowerCase();const o=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));if(!o)return {ok:false,status:'order_not_found'};const doors=await fetch(api+'/api/orcamento/'+encodeURIComponent(o.id)+'/portas',{headers:h}).then(r=>r.json());return {ok:doors?.success===true,id:o.id,numero_pedido:o.numero_pedido,cliente:o.cliente_nome,quantidade_total:o.quantidade_total,valor_total:Number(o.valor_total||0),portas:doors?.portas||[]};})()""" % json.dumps(ref)
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    return [colorglass_quote_create, colorglass_quote_add_door, colorglass_quote_verify, colorglass_quote_pdf]
