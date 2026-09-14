from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError

from meuharness.core.errors import HarnessError


def normalize_gateway_error(exc: Exception) -> HarnessError:
    """MAF can wrap SDK errors; inspect causes without exposing their text."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HarnessError):
            return current
        if isinstance(current, (APITimeoutError, TimeoutError)):
            return HarnessError("timeout", "Gateway request exceeded its deadline.", retryable=True)
        if isinstance(current, APIStatusError):
            status = current.status_code
            if status in (401, 403):
                code, message, retryable = (
                    "authentication",
                    "Gateway or provider refused authentication.",
                    False,
                )
            elif status == 404:
                code, message, retryable = (
                    "not_found",
                    "Gateway endpoint or model was not found.",
                    False,
                )
            elif status == 429:
                code, message, retryable = (
                    "rate_limited",
                    "Gateway or provider rate limit reached.",
                    True,
                )
            elif status >= 500:
                code, message, retryable = (
                    "gateway_error",
                    "Gateway/provider failed; inspect 9Router logs.",
                    True,
                )
            else:
                code, message, retryable = (
                    "request_rejected",
                    "Gateway rejected the request.",
                    False,
                )
            return HarnessError(code, message, retryable=retryable, status_code=status)
        if isinstance(current, APIConnectionError):
            return HarnessError(
                "connection", "Cannot connect to the model gateway.", retryable=True
            )
        current = current.__cause__ or current.__context__
    return HarnessError("runtime_error", "Model runtime failed; check adapter compatibility.")
