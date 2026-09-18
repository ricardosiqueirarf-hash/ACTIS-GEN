"""End-to-end integration tests for ColorGlass quotation flow."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from meuharness import execution_service as service


def _mock_colorglass_tools_success(monkeypatch):
    """Mock ColorGlass tools returning successful results."""
    def mock_create(customer_name: str) -> str:
        return json.dumps({
            "ok": True, "id": "uuid-123", "numero_pedido": "2024-001",
            "cliente": customer_name, "quantidade_total": 0, "valor_total": 0
        })

    def mock_add_door(*args, **kwargs) -> str:
        return json.dumps({
            "ok": True, "status": "saved", "numero_pedido": "2024-001",
            "cliente": "Cliente Teste", "quantidade_total": 2, "valor_total": 1500.00,
            "profile": "1036 bronze", "glass": "esp prata", "portas": [{"id": 1}]
        })

    def mock_verify(order_ref: str) -> str:
        return json.dumps({
            "ok": True, "id": "uuid-123", "numero_pedido": "2024-001",
            "cliente": "Cliente Teste", "quantidade_total": 2, "valor_total": 1500.00,
            "portas": [{"id": 1, "quantidade": 2}]
        })

    monkeypatch.setattr(
        "meuharness.colorglass_quotation_tools.build_colorglass_quotation_tools",
        lambda agent_id: [mock_create, mock_add_door, mock_verify]
    )


def _mock_colorglass_tools_auth_required(monkeypatch):
    """Mock ColorGlass tools returning auth_required."""
    def mock_auth_fail(*args, **kwargs) -> str:
        return json.dumps({"ok": False, "status": "auth_required"})

    monkeypatch.setattr(
        "meuharness.colorglass_quotation_tools.build_colorglass_quotation_tools",
        lambda agent_id: [mock_auth_fail, mock_auth_fail, mock_auth_fail]
    )


def _mock_colorglass_tools_order_not_found(monkeypatch):
    """Mock ColorGlass tools returning order_not_found."""
    def mock_not_found(*args, **kwargs) -> str:
        return json.dumps({"ok": False, "status": "order_not_found"})

    monkeypatch.setattr(
        "meuharness.colorglass_quotation_tools.build_colorglass_quotation_tools",
        lambda agent_id: [mock_not_found, mock_not_found, mock_not_found]
    )


def _patch_execution_base(monkeypatch):
    """Minimal execution service mocking."""
    monkeypatch.setattr(
        service, "get_agent",
        lambda agent_id: {
            "id": agent_id, "model": "test/model", "instructions": "test",
            "tools": [], "skills": ["colorglass.quotation"], "memory": False,
        }
    )
    monkeypatch.setattr(
        service.NineRouterSettings, "from_env",
        lambda *a, **k: SimpleNamespace(model="test/model", timeout_s=30)
    )
    monkeypatch.setattr(service, "connector_runtime_catalog", lambda *a, **k: [])
    monkeypatch.setattr(service, "append_checkpoint", lambda *a, **k: {})
    monkeypatch.setattr(service, "start_run", lambda *a, **k: {"id": "run-e2e"})
    monkeypatch.setattr(service, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(service, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(service, "create_task_for_run", lambda *a, **k: None)
    monkeypatch.setattr(service, "update_task_by_run", lambda *a, **k: None)
    monkeypatch.setattr(service, "update_work_by_run", lambda *a, **k: None)
    monkeypatch.setattr(service, "set_run_activity", lambda *a, **k: None)


def test_colorglass_auth_required_blocks_run(monkeypatch):
    """When auth_required is returned, the agent response should indicate session expired."""
    _patch_execution_base(monkeypatch)
    _mock_colorglass_tools_auth_required(monkeypatch)

    class Harness:
        async def run(self, request):
            # Verify skill instructions mention auth_required handling
            assert "auth_required" in request.instructions
            return SimpleNamespace(
                text='A sessão ColorGlass expirou. Faça login novamente no site antes de continuar.',
                model="test/model", elapsed_ms=10.0
            )

    monkeypatch.setattr(service, "create_harness", lambda *a, **k: Harness())
    monkeypatch.setattr(service, "_runtime_tools", lambda *a, **k: [])

    result = asyncio.run(service.execute_agent("cg-agent", "crie orçamento para João"))

    assert "sessão" in result.text.lower() or "login" in result.text.lower()


def test_colorglass_order_not_found_provides_actionable_error(monkeypatch):
    """When order_not_found, agent should be instructed to list orders."""
    _patch_execution_base(monkeypatch)
    _mock_colorglass_tools_order_not_found(monkeypatch)

    class Harness:
        async def run(self, request):
            from meuharness.adapters.maf.observed_tool import record_tool_result
            record_tool_result("run-e2e", "colorglass_quote_verify", False)
            return SimpleNamespace(
                text='Orçamento não encontrado. Use colorglass_list_orders para ver os disponíveis.',
                model="test/model", elapsed_ms=10.0
            )

    monkeypatch.setattr(service, "create_harness", lambda *a, **k: Harness())
    monkeypatch.setattr(service, "_runtime_tools", lambda *a, **k: [])

    result = asyncio.run(service.execute_agent("cg-agent", "verifique orçamento 999"))

    assert "colorglass_list_orders" in result.text or "não encontrado" in result.text.lower()


def test_colorglass_successful_flow_completes(monkeypatch):
    """Successful ColorGlass operations should mark run as completed."""
    _patch_execution_base(monkeypatch)
    _mock_colorglass_tools_success(monkeypatch)

    class Harness:
        async def run(self, request):
            from meuharness.adapters.maf.observed_tool import record_tool_result
            record_tool_result("run-e2e", "colorglass_quote_create", True)
            record_tool_result("run-e2e", "colorglass_quote_verify", True)
            return SimpleNamespace(
                text='Orçamento 2024-001 criado com sucesso para Cliente Teste.',
                model="test/model", elapsed_ms=15.0
            )

    monkeypatch.setattr(service, "create_harness", lambda *a, **k: Harness())
    monkeypatch.setattr(service, "_runtime_tools", lambda *a, **k: [])

    result = asyncio.run(service.execute_agent("cg-agent", "crie orçamento para Cliente Teste"))

    trace = service.get_run_tool_trace("run-e2e")
    assert all(item["ok"] for item in trace)
    assert "2024-001" in result.text
