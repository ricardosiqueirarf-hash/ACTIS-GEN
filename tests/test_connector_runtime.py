from __future__ import annotations

import json

from meuharness import connector_runtime


class _Response:
    status = 201

    def __init__(self, payload=b'{"id":123}'):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload


def _connector():
    return {
        "id": "erp",
        "name": "ERP",
        "kind": "http",
        "base_url": "https://erp.example/api",
        "auth_type": "bearer_env",
        "credential_ref": "ERP_TOKEN",
        "enabled": True,
        "agent_ids": ["worker"],
        "operations": [
            {"name": "get_order", "method": "GET", "path": "/orders/{id}", "mode": "read"},
            {"name": "create_order", "method": "POST", "path": "/orders", "mode": "write"},
        ],
    }


def test_normalize_operations_and_validation():
    connector = _connector()
    operations = connector_runtime.normalize_operations(connector)
    assert [item["name"] for item in operations] == ["get_order", "create_order"]
    assert operations[0]["mode"] == "read"
    assert operations[1]["mode"] == "write"

    invalid = [
        {"name": "bad", "method": "GET", "path": "https://evil.example/x", "mode": "read"}
    ]
    try:
        connector_runtime.validate_operations(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("absolute connector URL must be rejected")


def test_execute_operation_builds_authorized_request(monkeypatch):
    connector = _connector()
    operation = connector_runtime.normalize_operations(connector)[1]
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("ERP_TOKEN", "secret-test-token")
    monkeypatch.setattr(connector_runtime.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(connector_runtime.execute_operation(
        connector,
        operation,
        body_json='{"customer":"ACME"}',
        idempotency_key="order-123",
    ))
    request = seen["request"]
    assert result == {"ok": True, "status": 201, "data": {"id": 123}}
    assert request.full_url == "https://erp.example/api/orders"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer secret-test-token"
    assert request.headers["Idempotency-key"] == "order-123"


def test_catalog_and_tools_respect_agent_visibility(monkeypatch):
    connector = _connector()
    monkeypatch.setattr(connector_runtime, "list_connectors", lambda: [connector])
    monkeypatch.setattr(
        connector_runtime,
        "connector_visible_to_agent",
        lambda item, agent_id: agent_id == "worker",
    )

    catalog = connector_runtime.connector_runtime_catalog("worker")
    assert catalog[0]["id"] == "erp"
    assert len(catalog[0]["operations"]) == 2
    assert connector_runtime.connector_runtime_catalog("other") == []

    tools = connector_runtime.build_connector_tools("worker")
    assert {tool.__name__ for tool in tools} == {
        "connector_erp_get_order",
        "connector_erp_create_order",
    }
    assert connector_runtime.build_connector_tools("other") == []
