"""The only production module importing Microsoft Agent Framework."""

from __future__ import annotations

import json
from typing import Protocol

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from meuharness.core.contracts import HarnessRequest, RuntimeResult, TokenUsage
from meuharness.providers.openai_errors import normalize_gateway_error


class OpenAICompatibleGateway(Protocol):
    """Adapter-local SDK boundary, deliberately absent from core contracts."""

    @property
    def model(self) -> str: ...

    def open_client(self) -> AsyncOpenAI: ...


class MAFRuntime:
    name = "maf"

    def __init__(self, gateway: OpenAICompatibleGateway) -> None:
        self.gateway = gateway

    async def run(self, request: HarnessRequest) -> RuntimeResult:
        prompt = request.prompt
        if request.context:
            prompt += "\n\nContext (JSON data):\n" + json.dumps(
                request.context,
                ensure_ascii=False,
                allow_nan=False,
            )
        try:
            async with self.gateway.open_client() as sdk:
                client = OpenAIChatCompletionClient(model=self.gateway.model, async_client=sdk)
                async with Agent(
                    client=client,
                    name="MeuHarness",
                    instructions=request.instructions or None,
                ) as agent:
                    response = await agent.run(
                        prompt, options={"max_tokens": request.max_output_tokens}
                    )
                usage = response.usage_details or {}
                return RuntimeResult(
                    text=response.text,
                    model=self.gateway.model,
                    usage=TokenUsage(
                        input_tokens=usage.get("input_token_count"),
                        output_tokens=usage.get("output_token_count"),
                        total_tokens=usage.get("total_token_count"),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - sanitize errors at the public boundary
            raise normalize_gateway_error(exc) from None
