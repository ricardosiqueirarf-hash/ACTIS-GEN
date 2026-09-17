"""Idempotent first-party roster bootstrap for the embedded ACTIS Android Core.

The APK has its own SQLite database, so desktop runtime data isn't present on a fresh
Android install. This module guarantees the system General and the canonical
ColorGlass agents exist without overwriting user changes already made on-device.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from meuharness.agents import (
    create_agent,
    create_company,
    create_sector,
    list_agents,
    list_companies,
    list_sectors,
    update_agent,
)


def _norm(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _find_company() -> dict[str, Any] | None:
    for company in list_companies():
        token = _norm(company.get("name") or company.get("id"))
        if "colorglass" in token:
            return company
    return None


def _ensure_company() -> dict[str, Any]:
    company = _find_company()
    if company:
        return company
    return create_company({"name": "ColorGlass", "color": "#2f93ff"})


def _ensure_sector(company_id: str, name: str) -> dict[str, Any]:
    wanted = _norm(name)
    for sector in list_sectors(company_id):
        if _norm(sector.get("name")) == wanted:
            return sector
    return create_sector({"name": name, "company_id": company_id})


def _find_agent(*, preferred_id: str, name: str) -> dict[str, Any] | None:
    wanted_name = _norm(name)
    for agent in list_agents():
        if str(agent.get("id") or "") == preferred_id:
            return agent
        if _norm(agent.get("name")) == wanted_name:
            return agent
    return None


def _ensure_agent(spec: dict[str, Any], *, company_id: str | None = None, sector_id: str | None = None) -> dict[str, Any]:
    preferred_id = str(spec.pop("preferred_id"))
    agent = _find_agent(preferred_id=preferred_id, name=str(spec["name"]))
    if not agent:
        payload = dict(spec)
        payload["company_id"] = company_id
        payload["sector_id"] = sector_id
        return create_agent(payload)

    # Preserve on-device model/instructions/skills/customisation. Only repair the
    # canonical organization binding if an imported/migrated agent is detached.
    patch: dict[str, Any] = {}
    if company_id and not agent.get("company_id"):
        patch["company_id"] = company_id
        patch["sector_id"] = sector_id
    elif company_id and str(agent.get("company_id") or "") == company_id and sector_id and not agent.get("sector_id"):
        patch["sector_id"] = sector_id
    if not agent.get("company_capabilities") and spec.get("company_capabilities"):
        patch["company_capabilities"] = list(spec["company_capabilities"])
    return update_agent(str(agent["id"]), patch) if patch else agent


def ensure_android_default_roster() -> dict[str, Any]:
    """Ensure General + the canonical ColorGlass roster exist exactly once."""
    general = _ensure_agent(
        {
            "preferred_id": "general",
            "name": "General",
            "icon": "◆",
            "avatar_color": "#d7ff64",
            "avatar_role": "general",
            "avatar_expression": "confident",
            "instructions": (
                "Você é o General, agente padrão e orquestrador principal do ACTIS GEN. "
                "Administre agentes, contexto e operações do sistema e delegue tarefas ao agente especializado correto."
            ),
            "skills": ["actis.control-plane"],
            "company_capabilities": [],
            "memory": True,
        }
    )

    company = _ensure_company()
    commercial = _ensure_sector(str(company["id"]), "Comercial")

    agents = [
        _ensure_agent(
            {
                "preferred_id": "instagram-browser-bot",
                "name": "Instagram Browser Bot",
                "icon": "◎",
                "avatar_color": "#ec4899",
                "avatar_role": "browser",
                "avatar_expression": "focused",
                "instructions": "Agente ColorGlass para atendimento e operação autorizada do Instagram.",
                "skills": ["browser.instagram"],
                "company_capabilities": ["customer-intake"],
                "memory": True,
            },
            company_id=str(company["id"]),
            sector_id=str(commercial["id"]),
        ),
        _ensure_agent(
            {
                "preferred_id": "whatsapp-colorglass",
                "name": "WhatsApp ColorGlass",
                "icon": "◉",
                "avatar_color": "#22c55e",
                "avatar_role": "messenger",
                "avatar_expression": "attentive",
                "instructions": "Agente de atendimento WhatsApp da ColorGlass, com estado local e handoff humano.",
                "skills": ["whatsapp.standard-pack"],
                "company_capabilities": ["customer-intake"],
                "memory": True,
            },
            company_id=str(company["id"]),
            sector_id=str(commercial["id"]),
        ),
        _ensure_agent(
            {
                "preferred_id": "orcamentos-colorglass",
                "name": "Orçamentos ColorGlass",
                "icon": "◇",
                "avatar_color": "#f59e0b",
                "avatar_role": "quote",
                "avatar_expression": "focused",
                "instructions": "Agente especializado em criar e conferir orçamentos oficiais da ColorGlass.",
                "skills": ["colorglass.quotation"],
                "company_capabilities": ["quote", "orcamento"],
                "memory": True,
            },
            company_id=str(company["id"]),
            sector_id=str(commercial["id"]),
        ),
    ]

    return {
        "general": str(general.get("id") or "general"),
        "company": str(company.get("id") or ""),
        "agents": [str(agent.get("id") or "") for agent in agents],
    }
