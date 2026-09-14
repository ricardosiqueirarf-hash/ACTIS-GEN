from __future__ import annotations

import asyncio
import logging
import time

from .contracts import HarnessRequest, HarnessResult, RuntimeAdapter
from .errors import HarnessError

logger = logging.getLogger("meuharness")


class Harness:
    """One bounded run. No implicit history, automatic retry, or business state."""

    def __init__(self, runtime: RuntimeAdapter) -> None:
        self.runtime = runtime

    async def run(self, request: HarnessRequest) -> HarnessResult:
        started = time.monotonic()
        metadata = {"request_id": request.request_id, "runtime": self.runtime.name}
        logger.info("harness.run.started", extra=metadata)
        try:
            async with asyncio.timeout(request.timeout_s):
                response = await self.runtime.run(request)
                if not isinstance(response.text, str) or not response.text.strip():
                    raise HarnessError("invalid_response", "Runtime returned no usable text.")
            result = HarnessResult(
                request_id=request.request_id,
                text=response.text,
                model=response.model,
                runtime=self.runtime.name,
                elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                usage=response.usage,
            )
        except asyncio.CancelledError:
            logger.info("harness.run.cancelled", extra=metadata)
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize errors at the public boundary
            if isinstance(exc, TimeoutError):
                error = HarnessError("timeout", "Execution deadline exceeded.", retryable=True)
            elif isinstance(exc, HarnessError):
                error = exc
            else:
                error = HarnessError("runtime_error", "Runtime execution failed.")
            logger.warning(
                "harness.run.failed",
                extra={
                    **metadata,
                    "error_code": error.code,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
            raise error from None
        logger.info("harness.run.completed", extra={**metadata, "elapsed_ms": result.elapsed_ms})
        return result
