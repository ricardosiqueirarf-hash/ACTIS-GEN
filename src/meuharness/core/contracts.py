"""Small runtime-independent contracts for one explicit, stateless execution."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class HarnessRequest:
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""
    request_id: str = field(default_factory=lambda: str(uuid4()))
    timeout_s: float = 30.0
    max_output_tokens: int = 256

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(self.instructions, str):
            raise TypeError("instructions must be a string")
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be a non-empty string")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("timeout_s must be finite and positive")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if not isinstance(self.context, dict):
            raise TypeError("context must be a JSON object")
        try:
            snapshot = json.loads(json.dumps(self.context, allow_nan=False, ensure_ascii=False))
        except (TypeError, ValueError, RecursionError):
            raise ValueError("context must contain only finite JSON values") from None
        object.__setattr__(self, "context", snapshot)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class RuntimeResult:
    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class HarnessResult:
    request_id: str
    text: str
    model: str
    runtime: str
    elapsed_ms: float
    usage: TokenUsage = field(default_factory=TokenUsage)


class RuntimeAdapter(Protocol):
    name: str

    async def run(self, request: HarnessRequest) -> RuntimeResult: ...
