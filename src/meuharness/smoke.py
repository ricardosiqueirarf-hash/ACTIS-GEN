"""Minimal MAF -> 9Router smoke test."""

from __future__ import annotations

import asyncio
import os

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def _run() -> None:
    load_dotenv()

    base_url = os.getenv("NINEROUTER_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
    api_key = _required("NINEROUTER_API_KEY")
    model = _required("NINEROUTER_MODEL")

    client = OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    async with Agent(
        client=client,
        name="MeuHarnessSmoke",
        instructions=(
            "You are a connectivity test for meuharness. "
            "Answer briefly and do not call tools."
        ),
    ) as agent:
        result = await agent.run(
            "Responda exatamente: MAF -> 9Router funcionando."
        )

    print(result.text)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
