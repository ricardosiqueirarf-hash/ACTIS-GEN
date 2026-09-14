import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meuharness import Harness, HarnessError, HarnessRequest
from meuharness.adapters.maf import MAFRuntime
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings


@pytest.fixture
def endpoint():
    state = {"status": 200, "requests": [], "delay": 0, "empty": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond({"data": [{"id": "test/model"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"path": self.path, "body": body})
            time.sleep(state["delay"])
            self.respond(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test/model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "" if state["empty"] else "fixture response",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
                }
            )

        def respond(self, payload):
            if state["status"] != 200:
                payload = {"error": {"message": "secret-key-do-not-log", "type": "test"}}
            raw = json.dumps(payload).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway = NineRouterGateway(
        NineRouterSettings(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="secret-key-do-not-log",
            model="test/model",
            timeout_s=1,
        )
    )
    yield gateway, state
    server.shutdown()
    server.server_close()
    thread.join()


def test_catalog_and_direct_completion(endpoint):
    gateway, state = endpoint
    assert asyncio.run(gateway.list_models()) == ("test/model",)
    result = asyncio.run(gateway.complete("hello", max_output_tokens=50))
    assert result.text == "fixture response"
    assert result.usage.total_tokens == 10
    assert state["requests"][0]["path"] == "/v1/chat/completions"


def test_real_maf_adapter_serializes_context_without_shared_history(endpoint):
    gateway, state = endpoint

    async def run():
        harness = Harness(MAFRuntime(gateway))
        first = await harness.run(HarnessRequest("first", context={"item": 42}))
        second = await harness.run(HarnessRequest("second"))
        return first, second

    first, second = asyncio.run(run())
    assert first.text == second.text == "fixture response"
    assert first.request_id != second.request_id
    assert first.usage.total_tokens == 10
    assert first.runtime == "maf"
    assert '"item": 42' in json.dumps(state["requests"][0]["body"]).replace('\\"', '"')
    assert "first" not in json.dumps(state["requests"][1]["body"])
    assert len(state["requests"]) == 2


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication", False),
        (403, "authentication", False),
        (404, "not_found", False),
        (429, "rate_limited", True),
        (503, "gateway_error", True),
    ],
)
def test_http_failures_are_normalized_without_sdk_retries(endpoint, status, code, retryable):
    gateway, state = endpoint
    state["status"] = status
    with pytest.raises(HarnessError) as exc:
        asyncio.run(Harness(MAFRuntime(gateway)).run(HarnessRequest("x")))
    assert exc.value.code == code
    assert exc.value.retryable == retryable
    assert exc.value.status_code == status
    assert "secret-key" not in str(exc.value)
    assert len(state["requests"]) == 1


def test_real_maf_deadline(endpoint):
    gateway, state = endpoint
    state["delay"] = 0.4
    with pytest.raises(HarnessError) as exc:
        asyncio.run(Harness(MAFRuntime(gateway)).run(HarnessRequest("x", timeout_s=0.15)))
    assert exc.value.code == "timeout"


def test_empty_response_is_failure(endpoint):
    gateway, state = endpoint
    state["empty"] = True
    with pytest.raises(HarnessError) as exc:
        asyncio.run(Harness(MAFRuntime(gateway)).run(HarnessRequest("x")))
    assert exc.value.code == "invalid_response"


def test_settings_hide_key_and_environment_overrides_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NINEROUTER_API_KEY=secret\nNINEROUTER_MODEL=file/model\n")
    monkeypatch.setenv("NINEROUTER_MODEL", "override/model")
    monkeypatch.delenv("NINEROUTER_API_KEY", raising=False)
    settings = NineRouterSettings.from_env(env)
    assert settings.model == "override/model"
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com/v1",
        "http://localhost:20128",
        "ftp://localhost/v1",
        "http://localhost/v1?api_key=secret",
    ],
)
def test_unsafe_or_incomplete_endpoint_configuration(url):
    with pytest.raises(HarnessError):
        NineRouterSettings(base_url=url, api_key="test", model="test/model")


def test_catalog_can_be_discovered_before_selecting_model(endpoint):
    gateway, _ = endpoint
    settings = NineRouterSettings(base_url=gateway.settings.base_url, api_key="test", model="")
    unconfigured = NineRouterGateway(settings)
    assert asyncio.run(unconfigured.list_models()) == ("test/model",)
    with pytest.raises(HarnessError) as exc:
        asyncio.run(unconfigured.complete("hello"))
    assert exc.value.code == "configuration"


def test_smoke_stops_before_generation_for_unknown_model(endpoint, monkeypatch, capsys):
    from argparse import Namespace

    from meuharness.smoke import diagnose

    gateway, state = endpoint
    monkeypatch.setenv("NINEROUTER_BASE_URL", gateway.settings.base_url)
    monkeypatch.setenv("NINEROUTER_API_KEY", "secret-key-do-not-log")
    monkeypatch.setenv("NINEROUTER_MODEL", "missing/model")
    result = asyncio.run(
        diagnose(
            Namespace(
                env_file=None,
                model=None,
                timeout=1,
                list_models=False,
            )
        )
    )
    output = capsys.readouterr().out
    assert result == 1
    assert '"code": "model_not_listed"' in output
    assert '"stage": "maf_completion"' not in output
    assert "secret-key" not in output
    assert state["requests"] == []


def test_missing_explicit_env_file_is_actionable(tmp_path):
    with pytest.raises(HarnessError) as exc:
        NineRouterSettings.from_env(tmp_path / "missing.env")
    assert exc.value.code == "configuration"


def test_explicit_settings_override_invalid_environment(monkeypatch):
    monkeypatch.setenv("NINEROUTER_API_KEY", "test")
    monkeypatch.setenv("NINEROUTER_TIMEOUT_SECONDS", "invalid")
    monkeypatch.setenv("NINEROUTER_MODEL", "")
    settings = NineRouterSettings.from_env(model="test/model", timeout_s=2)
    assert settings.model == "test/model"
    assert settings.timeout_s == 2


def test_concurrent_runs_keep_context_separate(endpoint):
    gateway, state = endpoint

    async def run():
        harness = Harness(MAFRuntime(gateway))
        return await asyncio.gather(
            harness.run(HarnessRequest("task-alpha", context={"tenant": "alpha"})),
            harness.run(HarnessRequest("task-beta", context={"tenant": "beta"})),
        )

    results = asyncio.run(run())
    assert all(result.text == "fixture response" for result in results)
    assert len(state["requests"]) == 2
    for request in state["requests"]:
        body = json.dumps(request["body"])
        assert ("task-alpha" in body) != ("task-beta" in body)


def test_smoke_rejects_mismatched_reply_without_reporting_success(endpoint, monkeypatch, capsys):
    from argparse import Namespace

    from meuharness.smoke import diagnose

    gateway, state = endpoint
    monkeypatch.setenv("NINEROUTER_BASE_URL", gateway.settings.base_url)
    monkeypatch.setenv("NINEROUTER_API_KEY", "test")
    monkeypatch.setenv("NINEROUTER_MODEL", "test/model")
    result = asyncio.run(
        diagnose(
            Namespace(
                env_file=None,
                model=None,
                timeout=1,
                list_models=False,
            )
        )
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    direct = [event for event in events if event["stage"] == "direct_completion"]
    assert result == 1
    assert direct[-1]["code"] == "verification"
    assert all(event["status"] != "ok" for event in direct)
    assert len(state["requests"]) == 1
    assert not any(event["stage"] == "maf_completion" for event in events)
