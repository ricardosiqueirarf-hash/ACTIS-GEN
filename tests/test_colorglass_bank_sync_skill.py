import pytest

from meuharness.domains import colorglass_bank_sync as bank_sync
from meuharness.skills import _resolved_ids, progressive_skill_context


def test_finance_inherits_bank_sync_skill_with_progressive_disclosure():
    resolved = set(_resolved_ids(["colorglass.finance"]))
    assert "colorglass.finance.bank-sync" in resolved

    idle = progressive_skill_context(
        ["colorglass.finance"],
        "gere a DRE gerencial deste mês",
    )
    assert "# COLORGLASS FINANCE — BANK SYNC" not in idle

    active = progressive_skill_context(
        ["colorglass.finance"],
        "sincronize o banco Unicred e puxe extrato e DDA",
    )
    assert "# COLORGLASS FINANCE — BANK SYNC" in active
    assert "finance_bank_daily_sync()" in active
    assert "Nunca considerar uma captura parcial como sucesso." in active


def test_finance_runtime_exposes_closed_bank_sync_tools():
    from meuharness.domains.colorglass_finance import build_finance_tools
    names = {getattr(tool, "__name__", "") for tool in build_finance_tools("financeiro-colorglass")}
    assert {
        "finance_bank_sync_status",
        "finance_bank_prepare_login",
        "finance_bank_daily_sync",
        "finance_bank_transactions",
        "finance_dda_bills",
    } <= names


def test_daily_sync_stops_for_manual_auth(monkeypatch):
    monkeypatch.setattr(
        bank_sync,
        "bank_sync_status",
        lambda: {
            "authentication": "auth_required",
            "ready_for_sync": False,
        },
    )
    result = bank_sync.daily_bank_sync()
    assert result["ok"] is False
    assert result["complete"] is False
    assert result["status"] == "auth_required"
    assert result["human_action_required"] is True


def test_daily_sync_only_completes_after_extrato_and_dda_are_persisted(monkeypatch):
    monkeypatch.setattr(
        bank_sync,
        "bank_sync_status",
        lambda: {
            "authentication": "authenticated",
            "ready_for_sync": True,
        },
    )
    responses = iter([
        (
            0,
            {
                "ok": True,
                "partial": False,
                "extrato": {"fetched": 19, "new": 0, "existing": 19},
                "dda": {"ok": True, "fetched": 12, "new": 12, "existing": 0, "error": None},
            },
        ),
        (
            0,
            {
                "dda_total": 12,
                "last_sync": {"status": "ok"},
                "last_dda_sync": {"status": "ok"},
            },
        ),
    ])
    monkeypatch.setattr(bank_sync, "_run_json", lambda command, timeout: next(responses))

    result = bank_sync.daily_bank_sync()

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["partial"] is False
    assert result["extrato"]["persisted_status"] == "ok"
    assert result["dda"]["persisted_status"] == "ok"
    assert result["dda"]["active_count"] == 12


def test_daily_sync_fails_closed_when_dda_is_partial(monkeypatch):
    monkeypatch.setattr(
        bank_sync,
        "bank_sync_status",
        lambda: {
            "authentication": "authenticated",
            "ready_for_sync": True,
        },
    )
    responses = iter([
        (
            0,
            {
                "ok": True,
                "partial": True,
                "extrato": {"fetched": 19, "new": 0, "existing": 19},
                "dda": {
                    "ok": False,
                    "fetched": 0,
                    "new": 0,
                    "existing": 0,
                    "error": "DDA_CAPTURE_TIMEOUT",
                },
            },
        ),
        (
            0,
            {
                "dda_total": 7,
                "last_sync": {"status": "ok"},
                "last_dda_sync": {"status": "failed"},
            },
        ),
    ])
    monkeypatch.setattr(bank_sync, "_run_json", lambda command, timeout: next(responses))

    result = bank_sync.daily_bank_sync()

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["partial"] is True
    assert result["status"] == "partial_or_unverified"


def test_bank_bridge_wrapper_rejects_arbitrary_commands():
    with pytest.raises(ValueError):
        bank_sync._run_json("anything-else", timeout=1)
