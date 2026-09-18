from pathlib import Path

import pytest

from meuharness import storage
from meuharness.domains import colorglass_finance as finance
from meuharness.domains import colorglass_operations as ops
from meuharness.domains import colorglass_procurement as procurement
from meuharness.skills import runtime_features_for_skills


def _use_tmp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis.db")
    fake = lambda agent_id: {"id": agent_id, "company_id": "colorglass"}
    monkeypatch.setattr(ops, "get_agent", fake)
    monkeypatch.setattr(procurement, "get_agent", fake)
    monkeypatch.setattr(finance, "get_agent", fake)


def test_supplier_allowlist_blocks_unknown_contact(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    procurement.add_supplier("buy", "Alternativa", "5585999999999", categories=["perfil"])
    assert procurement.authorized_supplier("buy", "55 (85) 99999-9999")["name"] == "Alternativa"
    with pytest.raises(ValueError):
        procurement.authorized_supplier("buy", "558588888888")


def test_finance_report_tracks_cash_and_receivables(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    operation = ops.ensure_operation("fin", customer="Cemari", contact="55851111")
    finance.update_operation_finance("fin", operation["id"], total=1000, paid=250)
    today = finance.date.today().isoformat()
    event = finance.record_cash_event(
        "fin", kind="income", amount=250, description="Entrada Cemari",
        due_date=today, status="open", operation_ref=operation["id"],
    )
    finance.mark_cash_event("fin", event["id"], "paid")
    report = finance.daily_report("fin", today)
    assert report["cash_in"] == 250
    assert report["cash_out"] == 0
    assert report["open_receivables"] == 750


def test_domain_skills_expose_runtimes():
    procurement_features = runtime_features_for_skills(["colorglass.procurement"])
    finance_features = runtime_features_for_skills(["colorglass.finance"])
    assert "colorglass.operations.local" in procurement_features
    assert "colorglass.procurement.local" in procurement_features
    assert "colorglass.operations.local" in finance_features
    assert "colorglass.finance.local" in finance_features
