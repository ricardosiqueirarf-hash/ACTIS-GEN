import asyncio
import logging

import pytest

from meuharness import Harness, HarnessError, HarnessRequest
from meuharness.core.contracts import RuntimeResult


class FakeRuntime:
    name = "fake"

    async def run(self, request):
        return RuntimeResult(text="ok", model="test/model")


def test_public_api_is_runtime_independent(caplog):
    with caplog.at_level(logging.INFO, logger="meuharness"):
        result = asyncio.run(Harness(FakeRuntime()).run(HarnessRequest("private prompt")))
    assert result.text == "ok"
    assert result.runtime == "fake"
    assert result.elapsed_ms >= 0
    assert result.request_id
    assert "private prompt" not in caplog.text
    assert [record.msg for record in caplog.records] == [
        "harness.run.started",
        "harness.run.completed",
    ]


def test_deadline_cancels_runtime_and_reports_timeout(caplog):
    class SlowRuntime(FakeRuntime):
        cancelled = False

        async def run(self, request):
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True

    runtime = SlowRuntime()
    with caplog.at_level(logging.INFO, logger="meuharness"), pytest.raises(HarnessError) as exc:
        asyncio.run(Harness(runtime).run(HarnessRequest("x", timeout_s=0.02)))
    assert exc.value.code == "timeout"
    assert exc.value.retryable
    assert runtime.cancelled
    assert caplog.records[-1].msg == "harness.run.failed"


def test_caller_cancellation_propagates():
    class CancelledRuntime(FakeRuntime):
        async def run(self, request):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Harness(CancelledRuntime()).run(HarnessRequest("x")))


def test_unexpected_exception_does_not_leak_content():
    class BrokenRuntime(FakeRuntime):
        async def run(self, request):
            raise RuntimeError("secret-api-key and private business context")

    with pytest.raises(HarnessError) as exc:
        asyncio.run(Harness(BrokenRuntime()).run(HarnessRequest("x")))
    assert exc.value.code == "runtime_error"
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize(
    "fields",
    [
        {"prompt": " "},
        {"prompt": 123},
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"max_output_tokens": 0},
        {"max_output_tokens": True},
        {"context": {"bad": object()}},
        {"context": {"bad": float("nan")}},
    ],
)
def test_invalid_requests_fail_before_runtime(fields):
    with pytest.raises(ValueError):
        HarnessRequest(**{"prompt": "test", **fields})


def test_context_snapshot_is_not_mutated_by_caller():
    context = {"items": ["before"]}
    request = HarnessRequest("test", context=context)
    context["items"].append("after")
    assert request.context == {"items": ["before"]}
