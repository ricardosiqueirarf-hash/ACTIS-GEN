from pathlib import Path

from meuharness import storage
from meuharness.domains import colorglass_operations as ops
from meuharness.execution_service import _fast_quote_tool_choice
from meuharness.skills import runtime_features_for_skills


def _use_tmp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    monkeypatch.setattr(ops, "get_agent", lambda agent_id: {
        "id": agent_id,
        "company_id": "colorglass" if agent_id != "other" else "other-company",
    })


def test_operation_is_reused_for_same_active_contact(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    first = ops.ensure_operation("wa", customer="Cemari", contact="55859999")
    second = ops.ensure_operation("wa", customer="Cemari", contact="55859999")
    assert second["id"] == first["id"]
    assert len(ops.list_operations("wa")) == 1


def test_departments_share_one_canonical_operation(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    operation = ops.ensure_operation("wa", customer="Cemari", contact="55859999")
    ops.patch_department("wa", operation["id"], "procurement", {
        "status": "missing",
        "missing_materials": ["perfil 1036 prata"],
    })
    ops.patch_department("wa", operation["id"], "logistics", {
        "status": "scheduled",
        "address": "Fortaleza",
        "address_confirmed": True,
    })
    current = ops.get_operation("wa", operation["id"])
    assert current["procurement"]["missing_materials"] == ["perfil 1036 prata"]
    assert current["logistics"]["address_confirmed"] is True
    assert current["phase"] == "logistics"


def test_operations_are_company_scoped(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    ops.ensure_operation("wa", customer="Cemari", contact="55859999")
    assert len(ops.list_operations("wa")) == 1
    assert ops.list_operations("other") == []


def test_operations_skill_exposes_runtime():
    assert "colorglass.operations.local" in runtime_features_for_skills(["colorglass.operations"])


def test_fast_quote_route_does_not_force_create_for_alteration():
    prompt = (
        'ALTERAÇÃO DE ORÇAMENTO EXISTENTE. NÃO crie um novo orçamento. '
        'Payload: {"quote_mode":"alteration","order_ref":"510","operator_answer":"troque o vidro"}'
    )
    assert _fast_quote_tool_choice("or-amentos-colorglass", prompt) is None
