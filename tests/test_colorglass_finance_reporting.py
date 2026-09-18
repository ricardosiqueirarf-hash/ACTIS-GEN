import sqlite3
from pathlib import Path

from meuharness import storage
from meuharness.domains import colorglass_finance_reporting as reporting


def _setup(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "actis")
    monkeypatch.setattr(storage, "DB_FILE", tmp_path / "actis" / "actis.db")
    monkeypatch.setattr(
        reporting,
        "get_agent",
        lambda agent_id: {"id": agent_id, "company_id": "colorglass"},
    )
    bank = tmp_path / "bank.db"
    conn = sqlite3.connect(bank)
    conn.executescript(
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_key TEXT UNIQUE NOT NULL,
            posted_at TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            direction TEXT NOT NULL,
            description TEXT NOT NULL,
            balance_after_cents INTEGER,
            first_seen_at TEXT,
            last_seen_at TEXT
        );
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT, finished_at TEXT, status TEXT,
            fetched_count INTEGER, new_count INTEGER,
            existing_count INTEGER, error_code TEXT
        );
        """
    )
    conn.executemany(
        """INSERT INTO transactions
           (external_key,posted_at,amount_cents,direction,description,balance_after_cents)
           VALUES(?,?,?,?,?,?)""",
        [
            ("credit-1", "2026-09-17", 100000, "credit", "CLIENTE A", 500000),
            ("debit-1", "2026-09-17", -25000, "debit", "FORNECEDOR X", 475000),
        ],
    )
    conn.execute(
        """INSERT INTO sync_runs
           (finished_at,status,fetched_count,new_count,existing_count,error_code)
           VALUES(?,?,?,?,?,?)""",
        ("2026-09-17T20:00:00+00:00", "ok", 2, 2, 0, None),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(reporting, "BANK_DB_PATH", bank)
    return bank


def test_bank_source_is_read_only_and_reconciliation_is_separate(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rows = reporting.list_bank_transactions("fin")["transactions"]
    assert len(rows) == 2
    assert all(row["reconciliation_status"] == "unreconciled" for row in rows)

    proposed = reporting.reconcile_bank_transaction(
        "fin",
        bank_transaction_key="credit-1",
        account_code="3.1.01",
        target_type="receivable",
        target_ref="rec-1",
        human_confirmed=False,
    )
    assert proposed["status"] == "proposed"
    assert storage.load_collection(reporting.LEDGER_COLLECTION) == []

    confirmed = reporting.reconcile_bank_transaction(
        "fin",
        bank_transaction_key="credit-1",
        account_code="3.1.01",
        target_type="receivable",
        target_ref="rec-1",
        competence_date="2026-09-01",
        human_confirmed=True,
    )
    assert confirmed["status"] == "confirmed"
    ledger = storage.load_collection(reporting.LEDGER_COLLECTION)
    assert len(ledger) == 1
    assert ledger[0]["account_code"] == "3.1.01"
    assert ledger[0]["amount"] == 1000.0


def test_payable_competence_feeds_dre_and_bank_confirmation_marks_paid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    payable = reporting.record_payable(
        "fin",
        amount=250,
        description="Perfis",
        counterparty="Fornecedor X",
        due_date="2026-09-20",
        competence_date="2026-09-10",
        account_code="4.1.01",
    )
    before = reporting.dre_report(
        "fin", date_from="2026-09-01", date_to="2026-09-30"
    )
    assert before["cogs"] == 250.0

    reporting.reconcile_bank_transaction(
        "fin",
        bank_transaction_key="debit-1",
        account_code="4.1.01",
        target_type="payable",
        target_ref=payable["id"],
        competence_date="2026-09-10",
        human_confirmed=True,
    )
    paid = reporting.list_payables("fin", status="paid")
    assert len(paid) == 1
    assert paid[0]["bank_transaction_key"] == "debit-1"
    ledger = storage.load_collection(reporting.LEDGER_COLLECTION)
    assert len(ledger) == 1
    assert ledger[0]["status"] == "paid"


def test_cashflow_uses_bank_realized_and_open_forecast(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    reporting.record_payable(
        "fin",
        amount=300,
        description="Conta",
        counterparty="Fornecedor",
        due_date="2026-09-25",
        competence_date="2026-09-01",
        account_code="5.1.05",
    )
    receivables = {
        "ok": True,
        "itens": [
            {
                "id": "r1",
                "valor": 800,
                "vencimento": "2026-09-24",
                "status_categoria": "a_vencer",
            }
        ],
    }
    report = reporting.cashflow_report(
        "fin",
        as_of="2026-09-17",
        forecast_days=30,
        official_receivables=receivables,
    )
    assert report["bank_balance"] == 4750.0
    assert report["realized_month"]["inflows"] == 1000.0
    assert report["realized_month"]["outflows"] == 250.0
    assert report["forecast"]["receivables"] == 800.0
    assert report["forecast"]["payables"] == 300.0
    assert report["forecast"]["projected_bank_balance"] == 5250.0


def test_reconciliation_workspace_only_suggests(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    receivables = {
        "ok": True,
        "itens": [
            {
                "id": "r1",
                "numero_pedido": "513",
                "cliente_nome": "Cliente A",
                "valor": 1000,
                "status_categoria": "a_vencer",
            }
        ],
    }
    workspace = reporting.reconciliation_workspace(
        "fin", official_receivables=receivables
    )
    credit = next(
        item for item in workspace["workspace"]
        if item["bank_transaction"]["bank_transaction_key"] == "credit-1"
    )
    assert credit["unique_exact_candidate"]["target_ref"] == "r1"
    assert credit["unique_exact_candidate"]["match"] == "exact_amount_and_name"
    assert workspace["rule"] == "suggestions_only_human_confirms"


def test_accrual_can_be_paid_by_bank_without_duplicate_dre_entry(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    entry = reporting.record_accrual(
        "fin",
        kind="income",
        amount=1000,
        description="Venda pedido 513",
        competence_date="2026-09-10",
        account_code="3.1.01",
        source="erp",
        source_ref="513",
    )
    reporting.reconcile_bank_transaction(
        "fin",
        bank_transaction_key="credit-1",
        account_code="3.1.01",
        target_type="ledger",
        target_ref=entry["id"],
        competence_date="2026-09-10",
        human_confirmed=True,
    )
    ledger = storage.load_collection(reporting.LEDGER_COLLECTION)
    assert len(ledger) == 1
    assert ledger[0]["status"] == "paid"
    assert ledger[0]["bank_transaction_key"] == "credit-1"
    report = reporting.dre_report(
        "fin", date_from="2026-09-01", date_to="2026-09-30"
    )
    assert report["revenue"] == 1000.0


def test_confirmed_receivable_is_not_double_counted_in_forecast(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    reporting.reconcile_bank_transaction(
        "fin",
        bank_transaction_key="credit-1",
        account_code="3.1.01",
        target_type="receivable",
        target_ref="r1",
        human_confirmed=True,
    )
    receivables = {
        "ok": True,
        "itens": [
            {
                "id": "r1",
                "valor": 1000,
                "vencimento": "2026-09-24",
                "status_categoria": "a_vencer",
            }
        ],
    }
    report = reporting.cashflow_report(
        "fin",
        as_of="2026-09-17",
        forecast_days=30,
        official_receivables=receivables,
    )
    assert report["forecast"]["receivables"] == 0.0
    position = reporting.financial_position_report(
        "fin", official_receivables=receivables
    )
    assert position["receivables"]["adjusted_open_total"] == 0
