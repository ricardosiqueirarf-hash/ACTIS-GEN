from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Annotated, Any
from urllib.parse import quote

from meuharness.artifacts import register_artifact
from meuharness.browser_control import _cdp
from meuharness.native_browser_tools import _active, _value
from meuharness import storage

SITE = "https://www.colorglassfortaleza.com.br"

def _ensure_site_page(agent_id: str) -> str:
    """Guarantee the managed tab is on the ColorGlass origin before reading its session."""
    _, page, ws = _active(agent_id)
    url = str(page.get("url") or "")
    if not url.startswith(SITE):
        _cdp(ws, "Page.navigate", {"url": f"{SITE}/index_loja.html"}, timeout_s=8)
        _wait_ready(agent_id)
        _, _, ws = _active(agent_id)
    return ws

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
            ws = _ensure_site_page(agent_id)
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

def _normalize_search(text: str) -> str:
    """Normalize text for robust fuzzy matching (NFD, remove accents, lowercase, trim)."""
    import unicodedata
    norm = unicodedata.normalize("NFD", str(text or ""))
    no_accents = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    return no_accents.lower().strip()

def export_colorglass_quote_pdf(agent_id: str, order_ref: str, *, run_id: str | None = None) -> dict[str, Any]:
    """Export the official customer quote layout from Informações do orçamento as a local PDF."""
    ref = str(order_ref or "").strip()
    if not ref:
        raise ValueError("Referência do orçamento vazia.")
    resolve = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const h={'Authorization':'Bearer '+token};let resp;try{resp=await fetch(api+'/api/orcamentos',{headers:h});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(resp.status===401||resp.status===403)return {ok:false,status:'auth_required',http_status:resp.status};if(!resp.ok)return {ok:false,status:'network_error',http_status:resp.status};const list=await resp.json().catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().trim();const o=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));return o?{ok:true,id:o.id,numero_pedido:o.numero_pedido,cliente:o.cliente_nome,quantidade_total:o.quantidade_total,valor_total:Number(o.valor_total||0)}:{ok:false,status:'order_not_found'};})()""" % json.dumps(ref)
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
    artifact = None
    if run_id:
        artifact = register_artifact(
            path, run_id=run_id, agent_id=agent_id, name=filename, mime_type="application/pdf"
        )
    # Reaquece o runtime de orçamento sem esperar o carregamento; o próximo orçamento não começa da página de impressão.
    try:
        _cdp(ws, "Page.navigate", {"url": f"{SITE}/agent-portas-runtime.html"}, timeout_s=4)
    except Exception:
        pass
    return {**resolved, "ok": True, "status": "pdf_ready", "path": str(path), "filename": filename,
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "artifact_id": artifact.get("id") if artifact else None}

def build_colorglass_quotation_tools(agent_id: str, *, run_id: str | None = None) -> list[object]:
    def colorglass_quote_create(customer_name: Annotated[str, "Nome do cliente para a nova ficha de orçamento"]) -> str:
        """Crie uma ficha real de orçamento ColorGlass e devolva UUID/pedido verificados."""
        name = str(customer_name or "").strip()
        if not name:
            raise ValueError("Nome do cliente vazio.")
        expr = """(async()=>{const name=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const storeID=localStorage.getItem('USER_STOREID')||undefined;let res;try{res=await fetch(api+'/api/orcamento',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({cliente_nome:name,storeID})});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(res.status===401||res.status===403)return {ok:false,status:'auth_required',http_status:res.status};const data=await res.json().catch(()=>({}));if(!res.ok||data.success===false)return {ok:false,status:'create_failed',http_status:res.status,error:data.error||'falha ao criar ficha'};let lr;try{lr=await fetch(api+'/api/orcamentos',{headers:{'Authorization':'Bearer '+token}});}catch(e){return {ok:false,status:'network_error',created_id:data.id,error:String(e)}}if(lr.status===401||lr.status===403)return {ok:false,status:'auth_required',created_id:data.id,http_status:lr.status};if(!lr.ok)return {ok:false,status:'network_error',created_id:data.id,http_status:lr.status};const list=await lr.json().catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const item=rows.find(x=>String(x.id)===String(data.id));if(!item)return {ok:false,status:'verify_failed',created_id:data.id};return {ok:true,status:'verified',id:data.id,numero_pedido:item.numero_pedido??data.numero_pedido??null,cliente:item.cliente_nome||name,quantidade_total:item.quantidade_total??0,valor_total:Number(item.valor_total||0)};})()""" % json.dumps(name)
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

        resolve = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';let resp;try{resp=await fetch(api+'/api/orcamentos',{headers:{'Authorization':'Bearer '+token}});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(resp.status===401||resp.status===403)return {ok:false,status:'auth_required',http_status:resp.status};if(!resp.ok)return {ok:false,status:'network_error',http_status:resp.status};const list=await resp.json().catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().trim();const item=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));return item?{ok:true,id:item.id,numero_pedido:item.numero_pedido,cliente:item.cliente_nome}:{ok:false,status:'order_not_found',ref};})()""" % json.dumps(ref)
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

    def colorglass_quote_patch_existing(
        order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"],
        expected_before_fingerprint: Annotated[str, "Fingerprint retornado imediatamente antes por colorglass_quote_verify"],
        glass: Annotated[str, "Novo vidro por nome exato normalizado; vazio mantém o atual"] = "",
        additional_delta: Annotated[float, "Valor a somar ao valor adicional de cada porta elegível"] = 0.0,
        additional_only_if_present: Annotated[bool, "Aplicar delta apenas nas portas que já possuem valor adicional"] = True,
    ) -> str:
        """Edite portas existentes com controle de concorrência, cálculo oficial e verificação before/after."""
        ref = str(order_ref or "").strip()
        expected = str(expected_before_fingerprint or "").strip()
        glass_query = str(glass or "").strip()
        delta = float(additional_delta or 0.0)
        if not ref:
            raise ValueError("Referência do orçamento vazia.")
        if not expected:
            return json.dumps({"ok": False, "status": "needs_verification", "error": "Execute colorglass_quote_verify imediatamente antes e informe o fingerprint retornado."}, ensure_ascii=False)
        if not glass_query and abs(delta) < 1e-12:
            return json.dumps({"ok": False, "status": "invalid_patch", "error": "Informe glass e/ou additional_delta."}, ensure_ascii=False)

        resolve = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';let resp;try{resp=await fetch(api+'/api/orcamentos',{headers:{Authorization:'Bearer '+token}});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(resp.status===401||resp.status===403)return {ok:false,status:'auth_required',http_status:resp.status};if(!resp.ok)return {ok:false,status:'network_error',http_status:resp.status};const list=await resp.json().catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];const norm=s=>String(s??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();const item=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));return item?{ok:true,id:item.id,numero_pedido:item.numero_pedido,cliente:item.cliente_nome}:{ok:false,status:'order_not_found',ref};})()""" % json.dumps(ref)
        resolved = _eval(agent_id, resolve, await_promise=True)
        if not isinstance(resolved, dict) or not resolved.get("ok"):
            return json.dumps(resolved or {"ok": False, "status": "order_not_found"}, ensure_ascii=False)
        order_uuid = str(resolved["id"])

        _, _, ws = _active(agent_id)
        _cdp(ws, "Page.navigate", {"url": f"{SITE}/portas.html?orcamento_uuid={quote(order_uuid)}"})
        _wait_ready(agent_id)
        deadline = time.monotonic() + 16
        while time.monotonic() < deadline:
            ready = _eval(
                agent_id,
                "typeof carregarPortas==='function' && typeof editarPorta==='function' && typeof calcularPrecoPorta==='function' && typeof salvarPortasBackend==='function' && Array.isArray(todosVidros)",
            )
            if ready:
                break
            time.sleep(0.25)
        else:
            return json.dumps({"ok": False, "status": "ui_not_ready"}, ensure_ascii=False)

        cfg = {
            "expected": expected,
            "glass": glass_query,
            "delta": delta,
            "only_existing": bool(additional_only_if_present),
        }
        expr = r"""(async()=>{
const cfg=%s;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const norm=s=>String(s??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';
const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';
if(!token)return {ok:false,status:'auth_required'};
const headers={'Authorization':'Bearer '+token};
const request=async(url,options={})=>{let r;try{r=await fetch(url,{...options,headers:{...headers,...(options.headers||{})}});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(r.status===401||r.status===403)return {ok:false,status:'auth_required',http_status:r.status};if(!r.ok)return {ok:false,status:'network_error',http_status:r.status};return {ok:true,data:await r.json().catch(()=>({}))}};
const stable=p=>({tipo:p?.tipo||'',quantidade:Number(p?.quantidade||0),dados:p?.dados||{},preco:Number(p?.preco||0)});
const stableText=rows=>JSON.stringify((rows||[]).map(stable));
const fingerprint=rows=>{const txt=stableText(rows);let h=2166136261;for(let i=0;i<txt.length;i++){h^=txt.charCodeAt(i);h=Math.imul(h,16777619)}return ('00000000'+(h>>>0).toString(16)).slice(-8)};
const stripMutable=p=>{const d={...(p?.dados||{})};delete d.vidro;delete d.valor_adicional;return JSON.stringify({tipo:p?.tipo||'',quantidade:Number(p?.quantidade||0),dados:d})};
const doorUrl=api+'/api/orcamento/'+encodeURIComponent(ORCAMENTO_UUID)+'/portas';
const first=await request(doorUrl);if(!first.ok)return first;
const beforeRows=Array.isArray(first.data?.portas)?first.data.portas:[];
if(!beforeRows.length)return {ok:false,status:'no_doors'};
const beforeFingerprint=fingerprint(beforeRows);
if(beforeFingerprint!==cfg.expected)return {ok:false,status:'conflict',expected:cfg.expected,current:beforeFingerprint};
await carregarPortas();await wait(150);
if(!Array.isArray(portas)||portas.length!==beforeRows.length)return {ok:false,status:'ui_state_mismatch',reason:'door_count'};
if(fingerprint(portas)!==beforeFingerprint)return {ok:false,status:'conflict',reason:'ui_snapshot_differs',expected:beforeFingerprint,current:fingerprint(portas)};
if(cfg.glass && (!Array.isArray(todosVidros)||!todosVidros.length)){if(typeof carregarVidros==='function')await carregarVidros();await wait(100)}
let glassMatch=null;
if(cfg.glass){const matches=(todosVidros||[]).filter(v=>norm(v?.tipo)===norm(cfg.glass)||norm([v?.tipo,v?.espessura?String(v.espessura)+'mm':''].filter(Boolean).join(' '))===norm(cfg.glass));if(matches.length!==1)return {ok:false,status:'catalog_match_failed',query:cfg.glass,matches:matches.map(v=>({id:v.id,tipo:v.tipo,espessura:v.espessura}))};glassMatch=matches[0]}
const next=[];const changes=[];
for(let i=0;i<portas.length;i++){
  const p=portas[i];const d={...(p.dados||{})};const oldAdditional=Number(d.valor_adicional||0);const applyDelta=Math.abs(Number(cfg.delta||0))>1e-12&&(!cfg.only_existing||Math.abs(oldAdditional)>1e-12);const applyGlass=!!glassMatch;
  if(!applyDelta&&!applyGlass){next.push({...p,orcamento_uuid:ORCAMENTO_UUID});continue}
  try{editarPorta(p.id)}catch(e){return {ok:false,status:'ui_state_mismatch',index:i,error:String(e)}}
  await wait(1050);
  const must=[['largura',d.largura],['altura',d.altura],['perfil',d.perfil]];for(const [id,want] of must){const el=document.getElementById(id);if(want!=null&&String(want)!==''&&String(el?.value??'')!==String(want))return {ok:false,status:'ui_state_mismatch',index:i,field:id,expected:String(want),current:String(el?.value??'')}}
  if(d.sistemas){const el=document.getElementById('sistemas');if(!el||String(el.value)!==String(d.sistemas))return {ok:false,status:'ui_state_mismatch',index:i,field:'sistemas'}}
  if(applyGlass){const el=document.getElementById('vidro');if(!el)return {ok:false,status:'ui_state_mismatch',index:i,field:'vidro'};el.value=String(glassMatch.id);el.dispatchEvent(new Event('change',{bubbles:true}))}
  let newAdditional=oldAdditional;
  if(applyDelta){const el=document.getElementById('valor_adicional');if(!el)return {ok:false,status:'ui_state_mismatch',index:i,field:'valor_adicional'};newAdditional=oldAdditional+Number(cfg.delta||0);el.value=String(newAdditional);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}
  await wait(80);
  let price;try{price=Number(calcularPrecoPorta())}catch(e){return {ok:false,status:'calculation_failed',index:i,error:String(e)}}if(!Number.isFinite(price))return {ok:false,status:'calculation_failed',index:i};
  const nd={...d};if(applyGlass)nd.vidro=String(glassMatch.id);if(applyDelta)nd.valor_adicional=newAdditional.toFixed(2);
  next.push({...p,dados:nd,preco:price,orcamento_uuid:ORCAMENTO_UUID});
  changes.push({index:i+1,before:{vidro:d.vidro||'',valor_adicional:oldAdditional,preco:Number(p.preco||0)},after_expected:{vidro:nd.vidro||'',valor_adicional:Number(nd.valor_adicional||0),preco:price}})
}
const prewrite=await request(doorUrl);if(!prewrite.ok)return prewrite;const prewriteRows=Array.isArray(prewrite.data?.portas)?prewrite.data.portas:[];const prewriteFingerprint=fingerprint(prewriteRows);if(prewriteFingerprint!==beforeFingerprint)return {ok:false,status:'conflict',reason:'changed_before_write',expected:beforeFingerprint,current:prewriteFingerprint};
if(!changes.length)return {ok:true,status:'verified',numero_pedido:%s,cliente:%s,before_fingerprint:beforeFingerprint,after_fingerprint:beforeFingerprint,changed_count:0,changes:[]};
try{await salvarPortasBackend(next)}catch(e){return {ok:false,status:'save_failed',error:String(e),before_fingerprint:beforeFingerprint}};
await wait(150);
const afterRes=await request(doorUrl);if(!afterRes.ok)return {...afterRes,mutation_may_have_occurred:true,before_fingerprint:beforeFingerprint};const afterRows=Array.isArray(afterRes.data?.portas)?afterRes.data.portas:[];
if(afterRows.length!==beforeRows.length)return {ok:false,status:'verify_failed',reason:'door_count_changed',before:beforeRows.length,after:afterRows.length,mutation_may_have_occurred:true};
for(let i=0;i<beforeRows.length;i++){if(stripMutable(beforeRows[i])!==stripMutable(afterRows[i]))return {ok:false,status:'verify_failed',reason:'unexpected_field_change',index:i+1,mutation_may_have_occurred:true}}
for(const ch of changes){const a=afterRows[ch.index-1]||{};const ad=a.dados||{};if(ch.after_expected.vidro&&String(ad.vidro)!==String(ch.after_expected.vidro))return {ok:false,status:'verify_failed',reason:'glass_mismatch',index:ch.index,mutation_may_have_occurred:true};if(Math.abs(Number(ad.valor_adicional||0)-Number(ch.after_expected.valor_adicional||0))>0.011)return {ok:false,status:'verify_failed',reason:'additional_mismatch',index:ch.index,mutation_may_have_occurred:true};if(Math.abs(Number(a.preco||0)-Number(ch.after_expected.preco||0))>0.02)return {ok:false,status:'verify_failed',reason:'price_mismatch',index:ch.index,mutation_may_have_occurred:true};ch.after={vidro:ad.vidro||'',valor_adicional:Number(ad.valor_adicional||0),preco:Number(a.preco||0)}}
const listRes=await request(api+'/api/orcamentos');if(!listRes.ok)return {...listRes,mutation_may_have_occurred:true};const order=(Array.isArray(listRes.data?.orcamentos)?listRes.data.orcamentos:[]).find(x=>String(x.id)===String(ORCAMENTO_UUID));const afterFingerprint=fingerprint(afterRows);return {ok:true,status:'verified',numero_pedido:order?.numero_pedido??%s,cliente:order?.cliente_nome??%s,quantidade_total:order?.quantidade_total??afterRows.length,valor_total:Number(order?.valor_total||0),before_fingerprint:beforeFingerprint,after_fingerprint:afterFingerprint,changed_count:changes.length,glass:glassMatch?{id:glassMatch.id,tipo:glassMatch.tipo,espessura:glassMatch.espessura}:null,changes};
})()""" % (json.dumps(cfg, ensure_ascii=False), json.dumps(resolved.get("numero_pedido")), json.dumps(resolved.get("cliente")), json.dumps(resolved.get("numero_pedido")), json.dumps(resolved.get("cliente")))
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    def colorglass_quote_pdf(order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"]) -> str:
        """Exporte o PDF oficial 'Orçamento do cliente' da tela Informações do orçamento."""
        return json.dumps(export_colorglass_quote_pdf(agent_id, order_ref, run_id=run_id), ensure_ascii=False, default=str)

    def colorglass_quote_verify(order_ref: Annotated[str, "UUID, número do pedido ou nome exato do cliente"]) -> str:
        """Verifique orçamento e portas diretamente no backend oficial pela sessão autenticada."""
        ref = str(order_ref or "").strip()
        expr = """(async()=>{const ref=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const h={'Authorization':'Bearer '+token};const req=async url=>{let r;try{r=await fetch(url,{headers:h});}catch(e){return {ok:false,status:'network_error',error:String(e)}}if(r.status===401||r.status===403)return {ok:false,status:'auth_required',http_status:r.status};if(!r.ok)return {ok:false,status:'network_error',http_status:r.status};return {ok:true,data:await r.json().catch(()=>({}))}};const listRes=await req(api+'/api/orcamentos');if(!listRes.ok)return listRes;const rows=Array.isArray(listRes.data?.orcamentos)?listRes.data.orcamentos:[];const norm=s=>String(s??'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().trim();const o=rows.find(x=>String(x.id)===ref)||rows.find(x=>String(x.numero_pedido)===ref)||rows.find(x=>norm(x.cliente_nome)===norm(ref));if(!o)return {ok:false,status:'order_not_found'};const doorsRes=await req(api+'/api/orcamento/'+encodeURIComponent(o.id)+'/portas');if(!doorsRes.ok)return doorsRes;const doors=doorsRes.data;const stable=p=>({tipo:p.tipo,quantidade:Number(p.quantidade||0),dados:p.dados||{},preco:Number(p.preco||0)});const txt=JSON.stringify((doors?.portas||[]).map(stable));let hsh=2166136261;for(let i=0;i<txt.length;i++){hsh^=txt.charCodeAt(i);hsh=Math.imul(hsh,16777619)}const fingerprint=('00000000'+(hsh>>>0).toString(16)).slice(-8);return {ok:doors?.success===true,status:doors?.success===true?'verified':'verify_failed',id:o.id,numero_pedido:o.numero_pedido,cliente:o.cliente_nome,quantidade_total:o.quantidade_total,valor_total:Number(o.valor_total||0),fingerprint,portas:doors?.portas||[]};})()""" % json.dumps(ref)
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    def colorglass_list_orders(limit: Annotated[int, "Número máximo de orçamentos a listar"] = 20) -> str:
        """Liste os orçamentos recentes do sistema ColorGlass com ID, número, cliente e totais."""
        limit_safe = max(1, min(int(limit), 100))
        expr = """(async()=>{const limit=%d;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const h={'Authorization':'Bearer '+token};const resp=await fetch(api+'/api/orcamentos',{headers:h});if(resp.status===401||resp.status===403)return {ok:false,status:'auth_required'};if(!resp.ok)return {ok:false,status:'network_error',http_status:resp.status};const list=await resp.json().catch(()=>({}));const rows=Array.isArray(list?.orcamentos)?list.orcamentos:[];return {ok:true,count:rows.length,orders:rows.slice(0,limit).map(o=>({id:o.id,numero_pedido:o.numero_pedido,cliente:o.cliente_nome,quantidade_total:o.quantidade_total,valor_total:Number(o.valor_total||0)}))};})()""" % limit_safe
        return json.dumps(_eval(agent_id, expr, await_promise=True), ensure_ascii=False, default=str)

    return [colorglass_quote_create, colorglass_quote_add_door, colorglass_quote_patch_existing, colorglass_quote_verify, colorglass_quote_pdf, colorglass_list_orders]
