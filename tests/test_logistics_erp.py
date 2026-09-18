from pathlib import Path

from meuharness import storage
from meuharness.domains import logistics_erp as erp
from meuharness.skills import runtime_features_for_skills


def _use_tmp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    monkeypatch.setattr(erp, "get_agent", lambda agent_id: {
        "id": agent_id,
        "company_id": "company-b" if agent_id == "log-b" else "company-a",
    })


def test_logistics_board_tracks_stage_deadline_and_pending(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    order = erp.create_order("log-a", {
        "customer": "Cemari", "title": "Portas do apartamento", "reference": "154",
        "promised_date": "2000-01-01", "priority": "high",
    })
    assert order["stage"] == "aguardando"
    order = erp.add_pending("log-a", order["id"], "Confirmar endereço")
    assert order["pending_open"] == 1
    order = erp.update_order("log-a", order["id"], {"stage": "producao"})
    assert order["stage"] == "producao"
    board = erp.board_snapshot("log-a")
    assert board["stats"]["active"] == 1
    assert board["stats"]["overdue"] == 1
    assert board["stats"]["pending_open"] == 1


def test_logistics_erp_is_company_scoped(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    order = erp.create_order("log-a", {"customer": "A", "title": "Pedido A"})
    assert len(erp.board_snapshot("log-a")["orders"]) == 1
    assert erp.board_snapshot("log-b")["orders"] == []
    try:
        erp.get_order("log-b", order["id"])
    except ValueError as exc:
        assert "não encontrado" in str(exc)
    else:
        raise AssertionError("Pedido de outra empresa ficou visível")


def test_logistics_skill_exposes_local_runtime():
    assert "logistics.erp.local" in runtime_features_for_skills(["logistics.erp"])
