from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from meuharness.core.contracts import RuntimeResult, TokenUsage
from meuharness.core.errors import HarnessError
from meuharness.providers.openai_errors import normalize_gateway_error

from .config import NineRouterSettings


class NineRouterGateway:
    """9Router configuration, SDK lifecycle, and direct diagnostic requests.

    MAF consumes a new SDK client from open_client for every run, so SDK history
    and mutable framework state cannot leak between concurrent executions.
    """

    def __init__(self, settings: NineRouterSettings) -> None:
        self.settings = settings

    @property
    def model(self) -> str:
        if not self.settings.model:
            raise HarnessError("configuration", "Select NINEROUTER_MODEL from GET /v1/models.")
        return self.settings.model

    def open_client(self) -> AsyncOpenAI:
        # Explicit credentials and endpoint keep provider/OAuth details inside 9Router.
        return AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            max_retries=self.settings.max_retries,
        )

    async def list_models(self) -> tuple[str, ...]:
        try:
            async with asyncio.timeout(self.settings.timeout_s):
                async with self.open_client() as client:
                    page = await client.models.list()
                    if not isinstance(page.data, list) or any(
                        not isinstance(item.id, str) or not item.id.strip() for item in page.data
                    ):
                        raise HarnessError(
                            "invalid_response", "Gateway returned an invalid model catalog."
                        )
                    return tuple(item.id for item in page.data)
        except Exception as exc:  # noqa: BLE001 - sanitize errors at the public boundary
            raise normalize_gateway_error(exc) from None

    async def complete(self, prompt: str, *, max_output_tokens: int = 100) -> RuntimeResult:
        """Diagnostic only: direct SDK call isolates the gateway from MAF."""
        try:
            async with asyncio.timeout(self.settings.timeout_s):
                async with self.open_client() as client:
                    response = await client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_output_tokens,
                        stream=False,
                    )
                    text = response.choices[0].message.content if response.choices else None
                    if not isinstance(text, str) or not text.strip():
                        raise HarnessError("invalid_response", "Gateway returned no usable text.")
                    usage = response.usage
                    return RuntimeResult(
                        text=text,
                        model=self.model,
                        usage=TokenUsage(
                            input_tokens=usage.prompt_tokens if usage else None,
                            output_tokens=usage.completion_tokens if usage else None,
                            total_tokens=usage.total_tokens if usage else None,
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 - sanitize errors at the public boundary
            raise normalize_gateway_error(exc) from None
