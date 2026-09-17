"""The only production module importing Microsoft Agent Framework."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Protocol

from agent_framework import Agent, Content, Message
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from meuharness.core.contracts import HarnessRequest, RuntimeResult, TokenUsage
from meuharness.providers.openai_errors import normalize_gateway_error

LOGGER = logging.getLogger("meuharness.maf")


class OpenAICompatibleGateway(Protocol):
    """Adapter-local SDK boundary, deliberately absent from core contracts."""

    @property
    def model(self) -> str: ...

    def open_client(self) -> AsyncOpenAI: ...


class MAFRuntime:
    name = "maf"

    def __init__(self, gateway: OpenAICompatibleGateway, tools: object | None = None) -> None:
        self.gateway = gateway
        self.tools = tools

    async def run(self, request: HarnessRequest) -> RuntimeResult:
        prompt = request.prompt
        if request.context:
            prompt += "\n\nContext (JSON data):\n" + json.dumps(
                request.context,
                ensure_ascii=False,
                allow_nan=False,
            )
        try:
            async with AsyncExitStack() as stack:
                tools = list(self.tools or [])
                for i, tool in enumerate(tools):
                    enter = getattr(tool, "__aenter__", None)
                    exit_ = getattr(tool, "__aexit__", None)
                    if callable(enter) and callable(exit_):
                        tools[i] = await stack.enter_async_context(tool)
                sdk = await stack.enter_async_context(self.gateway.open_client())
                client = OpenAIChatCompletionClient(model=self.gateway.model, async_client=sdk)
                agent = await stack.enter_async_context(
                    Agent(
                        client=client,
                        name="ACTIS GEN",
                        instructions=request.instructions or None,
                        tools=tools or None,
                    )
                )
                run_input: str | Message = prompt
                if request.attachments:
                    contents: list[Content | str] = [prompt]
                    for attachment in request.attachments:
                        uri = str(attachment.get("data_url") or attachment.get("uri") or "").strip()
                        media_type = str(attachment.get("media_type") or "").strip() or None
                        if uri:
                            contents.append(Content.from_uri(uri=uri, media_type=media_type))
                    run_input = Message("user", contents)
                run_options: dict[str, object] = {"max_tokens": request.max_output_tokens}
                if request.tool_choice is not None:
                    run_options["tool_choice"] = request.tool_choice
                response = await agent.run(run_input, options=run_options)
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
        except Exception as exc:
            LOGGER.exception(
                "MAF runtime failure model=%s error_type=%s",
                self.gateway.model,
                type(exc).__name__,
            )
            raise normalize_gateway_error(exc) from None
