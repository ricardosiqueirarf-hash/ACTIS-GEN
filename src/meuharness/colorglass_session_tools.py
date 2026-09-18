"""Read-only ColorGlass session/identity tools for safe runtime validation."""
from __future__ import annotations

import json
from typing import Annotated

from meuharness.native_browser_tools import _value
from meuharness.colorglass_quotation_tools import _ensure_site_page


def build_colorglass_session_tools(agent_id: str) -> list[object]:
    """Build read-only session validation tools that never expose tokens/passwords."""

    def colorglass_session_info() -> str:
        """Valide a sessão ColorGlass e retorne apenas metadados não sensíveis: autenticado, store_id, nível de acesso e quantidade de orçamentos visíveis."""
        expr = """(async()=>{
const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';
if(!token)return {ok:false,status:'auth_required',authenticated:false,message:'Nenhuma sessão de autenticação encontrada. Faça login no ColorGlass.'};
const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';
const storeID=localStorage.getItem('USER_STOREID')||null;
const level=localStorage.getItem('USER_LEVEL')||localStorage.getItem('ADMIN_LEVEL')||null;
const resp=await fetch(api+'/api/orcamentos',{headers:{'Authorization':'Bearer '+token}});
if(resp.status===401||resp.status===403)return {ok:false,status:'auth_required',authenticated:false,message:'Sessão expirada ou inválida. Faça login novamente.'};
if(!resp.ok)return {ok:false,status:'network_error',authenticated:true,store_id:storeID,level:level,orders_visible:'unknown',message:'Sessão autenticada, mas houve erro ao consultar orçamentos.'};
const list=await resp.json().catch(()=>({}));
const count=Array.isArray(list?.orcamentos)?list.orcamentos.length:0;
return {ok:true,status:'verified',authenticated:true,store_id:storeID,level:level,orders_visible:count,message:'Sessão ativa e válida.'};
})()"""
        try:
            ws = _ensure_site_page(agent_id)
            result = _value(ws, expr, await_promise=True)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"ok": False, "status": "network_error", "authenticated": False, "error": str(exc)}, ensure_ascii=False)

    return [colorglass_session_info]
