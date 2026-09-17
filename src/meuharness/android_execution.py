"""Pure-Python ACTIS agent runtime for the embedded Android Core.

This intentionally avoids Microsoft Agent Framework and native-extension dependencies.
It speaks to any OpenAI-compatible gateway over HTTP using only the standard library.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meuharness.agents import finish_run, get_agent, start_run
from meuharness.event_bus import emit_event
from meuharness.memory import recall, remember
from meuharness.storage import DATA_DIR

PROVIDER_FILE = DATA_DIR / "android-provider.json"


@dataclass(frozen=True, slots=True)
class AndroidExecutionResult:
    text: str
    model: str
    elapsed_ms: float | None
    run_id: str
    effective_tools: tuple[str, ...] = ()


def _load_provider() -> dict[str, str]:
    stored: dict[str, str] = {}
    if PROVIDER_FILE.is_file():
        try:
            value = json.loads(PROVIDER_FILE.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                stored = {str(k): str(v) for k, v in value.items() if v is not None}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            stored = {}
    return {
        "base_url": str(os.environ.get("ACTIS_ANDROID_LLM_BASE_URL") or stored.get("base_url") or os.environ.get("NINEROUTER_BASE_URL") or "").rstrip("/"),
        "api_key": str(os.environ.get("ACTIS_ANDROID_LLM_API_KEY") or stored.get("api_key") or os.environ.get("NINEROUTER_API_KEY") or ""),
        "model": str(os.environ.get("ACTIS_ANDROID_LLM_MODEL") or stored.get("model") or os.environ.get("NINEROUTER_MODEL") or ""),
    }


def provider_status() -> dict[str, Any]:
    cfg = _load_provider()
    return {
        "configured": bool(cfg["base_url"] and cfg["model"]),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg["api_key"]),
        "runtime": "android-embedded",
    }


def save_provider(data: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = _load_provider()
    submitted_key = str(data.get("api_key") or "").strip()
    api_key = current["api_key"] if submitted_key == "__KEEP__" else submitted_key
    payload = {
        "base_url": str(data.get("base_url") or current["base_url"] or "").strip().rstrip("/"),
        "api_key": api_key,
        "model": str(data.get("model") if "model" in data else current["model"] or "").strip(),
    }
    PROVIDER_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return provider_status()


def _endpoint(base: str, suffix: str) -> str:
    if base.endswith("/v1"):
        return base + suffix
    return base + "/v1" + suffix


def list_models_http(timeout: float = 8.0) -> list[str]:
    cfg = _load_provider()
    if not cfg["base_url"]:
        models = [str(a.get("model") or "").strip() for a in __import__("meuharness.agents", fromlist=["list_agents"]).list_agents()]
        if cfg["model"]:
            models.append(cfg["model"])
        return sorted({m for m in models if m})
    headers = {"Accept": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    request = urllib.request.Request(_endpoint(cfg["base_url"], "/models"), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
        items = value.get("data", []) if isinstance(value, dict) else []
        return sorted({str(item.get("id") or "") for item in items if isinstance(item, dict) and item.get("id")})
    except Exception:
        # Reachability is part of health on Android. Do not make a dead gateway
        # look alive merely because a model name was previously selected.
        return []


def _messages(agent: dict[str, Any], prompt: str, history: list[dict[str, Any]] | None, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    instructions = str(agent.get("instructions") or "").strip()
    memories = recall(str(agent.get("id") or ""), prompt, limit=6) if agent.get("memory", True) else []
    if memories:
        instructions += "\n\nMEMÓRIA RELEVANTE DO ACTIS:\n" + "\n".join("- " + str(m.get("content") or "") for m in memories)
    instructions += "\n\nVocê está rodando no ACTIS Android Embedded Core. Recursos Linux/Desktop podem estar indisponíveis neste dispositivo."
    result: list[dict[str, Any]] = []
    if instructions:
        result.append({"role": "system", "content": instructions})
    for item in (history or [])[-16:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content})
    if not result or result[-1].get("role") != "user" or str(result[-1].get("content") or "") != prompt:
        result.append({"role": "user", "content": prompt})
    if attachments:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for item in attachments[:4]:
            data_url = str(item.get("data_url") or "")
            if data_url.startswith("data:image/"):
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        result[-1] = {"role": "user", "content": content}
    return result


def _chat_http(model: str, messages: list[dict[str, Any]], timeout: float = 90.0) -> str:
    cfg = _load_provider()
    if not cfg["base_url"]:
        raise RuntimeError("Modelo ainda não configurado neste Android. Configure o 9Router no ACTIS Config.")
    selected = model or cfg["model"]
    if not selected:
        raise RuntimeError("Nenhum modelo foi selecionado para este agente.")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    payload = json.dumps({"model": selected, "messages": messages, "stream": False}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(_endpoint(cfg["base_url"], "/chat/completions"), data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Gateway retornou HTTP {exc.code}: {body}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível alcançar o gateway de IA: {exc.reason}") from None
    choices = value.get("choices") if isinstance(value, dict) else None
    if not choices or not isinstance(choices, list):
        raise RuntimeError("Gateway não retornou uma resposta válida.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    text = str((message or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError("Gateway retornou uma resposta vazia.")
    return text


async def execute_agent_android(
    agent_id: str,
    prompt: str,
    *,
    history: list[dict[str, Any]] | None = None,
    source: str = "user",
    conversation_id: str | None = None,
    agent_refs: list[Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    env_file: str | None = None,
    parent_agent_id: str | None = None,
) -> AndroidExecutionResult:
    del agent_refs, env_file, parent_agent_id
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado.")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt vazio.")
    cfg = _load_provider()
    model = str(agent.get("model") or cfg["model"] or "").strip()
    run = start_run(agent_id, prompt, model or "android-unconfigured", source=source, conversation_id=conversation_id)
    run_id = str(run["id"])
    emit_event("run.started", run_id=run_id, agent_id=agent_id, source=source, runtime="android-embedded")
    started = time.perf_counter()
    try:
        text = _chat_http(model, _messages(agent, prompt, history, attachments))
        elapsed = (time.perf_counter() - started) * 1000.0
        finish_run(run_id, response=text, elapsed_ms=elapsed)
        if agent.get("memory", True):
            remember(agent_id, "Usuário: " + prompt, conversation_id=conversation_id)
            remember(agent_id, "Assistente: " + text, conversation_id=conversation_id)
        emit_event("run.completed", run_id=run_id, agent_id=agent_id, model=model, elapsed_ms=elapsed, runtime="android-embedded")
        return AndroidExecutionResult(text=text, model=model, elapsed_ms=elapsed, run_id=run_id, effective_tools=tuple(agent.get("tools") or ()))
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        finish_run(run_id, elapsed_ms=elapsed, error=str(exc))
        emit_event("run.failed", run_id=run_id, agent_id=agent_id, error_type=type(exc).__name__, runtime="android-embedded")
        try:
            setattr(exc, "actis_run_id", run_id)
        except Exception:
            pass
        raise


def cancel_active_run_android(run_id: str) -> bool:
    # Embedded HTTP calls are synchronous. Marking the run cancelled keeps state
    # correct; hard interruption can be added when provider streaming is enabled.
    from meuharness.agents import cancel_run_record
    return cancel_run_record(run_id, "Execução cancelada no Android.")
