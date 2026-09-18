"""Low-latency native browser tools backed by the ACTIS managed Chrome/CDP session."""
from __future__ import annotations

import json
import time
from typing import Annotated, Any

from meuharness.browser_control import _cdp, _page, _targets
from meuharness.browser_manager import ensure_browser


def _active(agent_id: str) -> tuple[Any, dict[str, Any], str]:
    runtime, page = _page(agent_id)
    return runtime, page, str(page["webSocketDebuggerUrl"])


def _value(ws: str, expression: str, *, await_promise: bool = False) -> Any:
    result = _cdp(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": await_promise,
    }, timeout_s=20.0 if await_promise else 5.0)
    payload = result.get("result") or {}
    exception = result.get("exceptionDetails") or {}
    if exception:
        exc = exception.get("exception") or {}
        description = exc.get("description") or exception.get("text") or "Erro JavaScript"
        raise RuntimeError(str(description))
    if payload.get("subtype") == "error":
        raise RuntimeError(str(payload.get("description") or "Erro JavaScript"))
    return payload.get("value")


def build_native_browser_tools(agent_id: str) -> list[object]:
    """Expose the essential Browser Harness actions as native function tools."""

    def browser_page_info() -> str:
        """Leia URL, título, texto visível e controles principais da aba atual."""
        _, _, ws = _active(agent_id)
        raw = _value(ws, r'''JSON.stringify({
          url:location.href,title:document.title,readyState:document.readyState,
          viewport:{width:innerWidth,height:innerHeight},
          text:(document.body?.innerText||'').slice(0,7000),
          inputs:[...document.querySelectorAll('input,textarea,select')].slice(0,80).map(e=>({tag:e.tagName.toLowerCase(),type:e.type||'',id:e.id||'',name:e.name||'',placeholder:e.placeholder||'',value:e.type==='password'?'':e.value||''})),
          buttons:[...document.querySelectorAll('button')].slice(0,80).map(e=>({id:e.id||'',text:(e.innerText||'').trim(),disabled:!!e.disabled})),
          links:[...document.querySelectorAll('a')].slice(0,80).map(e=>({text:(e.innerText||'').trim(),href:e.href||''}))
        })''')
        return raw or "{}"

    def browser_goto(url: Annotated[str, "URL completa para abrir"]) -> str:
        """Navegue a aba atual para uma URL."""
        _, _, ws = _active(agent_id)
        target = str(url or "").strip()
        if not target:
            raise ValueError("URL vazia")
        if "://" not in target:
            target = "https://" + target
        _cdp(ws, "Page.navigate", {"url": target})
        return json.dumps({"ok": True, "url": target}, ensure_ascii=False)

    def browser_fill(selector: Annotated[str, "Seletor CSS do campo"], text: Annotated[str, "Texto a preencher"]) -> str:
        """Preencha um input/textarea/select por seletor CSS e dispare input/change."""
        _, _, ws = _active(agent_id)
        expr = """(()=>{const e=document.querySelector(%s);if(!e)return {ok:false,error:'element_not_found'};e.focus();const v=%s;if(e.tagName==='SELECT'){e.value=v}else{const p=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e),'value');if(p&&p.set)p.set.call(e,v);else e.value=v}e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return {ok:true,id:e.id||'',tag:e.tagName}})()""" % (json.dumps(selector), json.dumps(text))
        return json.dumps(_value(ws, expr), ensure_ascii=False)

    def browser_click(target: Annotated[str, "Seletor CSS, texto visível, aria-label ou trecho do onclick"]) -> str:
        """Clique por CSS ou, se não houver match, encontre botão/link pelo texto visível/aria/onclick."""
        _, _, ws = _active(agent_id)
        raw = str(target or "").strip()
        if not raw:
            raise ValueError("Alvo vazio")
        expr = r"""(()=>{
          const q=%s; let e=null;
          try{e=document.querySelector(q)}catch(_){}
          if(!e){
            const norm=s=>(s||'').replace(/\s+/g,' ').trim().toLowerCase();
            const want=norm(q);
            const nodes=[...document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')];
            e=nodes.find(n=>norm(n.innerText||n.value)===want)
              ||nodes.find(n=>norm(n.getAttribute('aria-label'))===want)
              ||nodes.find(n=>norm(n.innerText||n.value).includes(want))
              ||nodes.find(n=>norm(n.getAttribute('onclick')).includes(want));
          }
          if(!e)return {ok:false,error:'element_not_found',target:q};
          e.scrollIntoView({block:'center'});e.click();
          return {ok:true,id:e.id||'',text:(e.innerText||e.value||'').trim().slice(0,200),tag:e.tagName};
        })()""" % json.dumps(raw)
        return json.dumps(_value(ws, expr), ensure_ascii=False)

    def browser_press(key: Annotated[str, "Tecla, por exemplo Enter, Tab, Escape"]) -> str:
        """Pressione uma tecla na aba atual."""
        _, _, ws = _active(agent_id)
        key = str(key or "Enter")
        code = "Enter" if key.lower() in {"enter", "return"} else key
        _cdp(ws, "Input.dispatchKeyEvent", {"type": "keyDown", "key": code, "code": code})
        _cdp(ws, "Input.dispatchKeyEvent", {"type": "keyUp", "key": code, "code": code})
        return json.dumps({"ok": True, "key": code})

    def browser_wait(seconds: Annotated[float, "Segundos para aguardar"] = 1.0) -> str:
        """Aguarde alguns segundos antes de observar novamente."""
        time.sleep(max(0.0, min(float(seconds), 20.0)))
        return json.dumps({"ok": True, "waited": float(seconds)})

    def browser_wait_for_load(timeout_s: Annotated[float, "Timeout em segundos"] = 15.0) -> str:
        """Aguarde document.readyState=complete na aba atual."""
        deadline = time.monotonic() + max(0.5, min(float(timeout_s), 60.0))
        last = ""
        while time.monotonic() < deadline:
            _, _, ws = _active(agent_id)
            last = str(_value(ws, "document.readyState") or "")
            if last == "complete":
                return json.dumps({"ok": True, "readyState": last})
            time.sleep(0.25)
        return json.dumps({"ok": False, "error": "load_timeout", "readyState": last})

    def browser_wait_for_element(selector: Annotated[str, "Seletor CSS"], timeout_s: Annotated[float, "Timeout em segundos"] = 10.0) -> str:
        """Aguarde um elemento CSS existir no DOM."""
        deadline = time.monotonic() + max(0.5, min(float(timeout_s), 60.0))
        while time.monotonic() < deadline:
            _, _, ws = _active(agent_id)
            exists = bool(_value(ws, "!!document.querySelector(%s)" % json.dumps(selector)))
            if exists:
                return json.dumps({"ok": True, "selector": selector})
            time.sleep(0.25)
        return json.dumps({"ok": False, "error": "element_timeout", "selector": selector})

    def browser_js(expression: Annotated[str, "Expressão JavaScript somente para observar/verificar a página"]) -> str:
        """Avalie JavaScript na aba para leitura/verificação determinística do estado."""
        _, _, ws = _active(agent_id)
        value = _value(ws, str(expression or ""), await_promise=True)
        return json.dumps({"ok": True, "value": value}, ensure_ascii=False, default=str)

    def browser_list_tabs() -> str:
        """Liste abas reais abertas no Chrome persistente deste agente."""
        runtime = ensure_browser(agent_id)
        rows = [{"targetId": x.get("id"), "url": x.get("url"), "title": x.get("title")} for x in _targets(runtime.cdp_url) if x.get("type") == "page"]
        return json.dumps(rows, ensure_ascii=False)

    def browser_current_tab() -> str:
        """Retorne a URL e o título da aba atual."""
        _, page, _ = _active(agent_id)
        return json.dumps({"targetId": page.get("id"), "url": page.get("url"), "title": page.get("title")}, ensure_ascii=False)

    return [browser_page_info, browser_goto, browser_fill, browser_click, browser_press,
            browser_wait, browser_wait_for_load, browser_wait_for_element, browser_js,
            browser_list_tabs, browser_current_tab]
