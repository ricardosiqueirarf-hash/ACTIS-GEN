"""Bank reconciliation, payables, chart of accounts and management reporting for ColorGlass."""
from __future__ import annotations

import os
import sqlite3
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from meuharness.agents import get_agent
from meuharness.storage import load_collection, mutate_collection

BANK_DB_PATH = Path(os.getenv(
    "ACTIS_UNICRED_BANK_DB",
    str(Path.home() / ".local" / "share" / "unicred-bank-bridge" / "bank.db"),
))
RECONCILIATION_COLLECTION = "colorglass_bank_reconciliations"
PAYABLE_COLLECTION = "colorglass_accounts_payable"
ACCOUNT_COLLECTION = "colorglass_chart_of_accounts"
LEDGER_COLLECTION = "colorglass_cash_events"

DEFAULT_ACCOUNTS = (
    ("1.1.01", "Adiantamentos a empregados", "asset", "non_dre", "operating"),
    ("3.1.01", "Receita de vendas", "income", "revenue", "operating"),
    ("4.1.01", "CMV - Alumínio", "expense", "cogs", "operating"),
    ("4.1.02", "CMV - Vidros", "expense", "cogs", "operating"),
    ("4.1.03", "CMV - Ferragens e acessórios", "expense", "cogs", "operating"),
    ("4.1.04", "CMV - Produção e terceirização", "expense", "cogs", "operating"),
    ("5.1.01", "Despesas com pessoal", "expense", "opex", "operating"),
    ("5.1.02", "Ocupação e aluguel", "expense", "opex", "operating"),
    ("5.1.03", "Energia e utilidades", "expense", "opex", "operating"),
    ("5.1.04", "Comercial, marketing e comissões", "expense", "opex", "operating"),
    ("5.1.05", "Administrativo", "expense", "opex", "operating"),
    ("5.1.06", "Logística e fretes", "expense", "opex", "operating"),
    ("5.2.01", "Despesas financeiras", "expense", "financial", "financial"),
    ("6.1.01", "Tributos e impostos", "expense", "taxes", "tax"),
    ("7.1.01", "Investimentos / ativo imobilizado", "expense", "non_dre", "investing"),
    ("8.1.01", "Transferência entre contas", "transfer", "non_dre", "transfer"),
    ("9.1.01", "Sócios / retiradas / aportes", "equity", "non_dre", "financing"),
    ("9.9.99", "Não classificado", "other", "non_dre", "unclassified"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _company_for_agent(agent_id: str) -> str:
    agent = get_agent(agent_id)
    if not agent:
        raise ValueError("Agente não encontrado")
    company_id = str(agent.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("Agente sem empresa")
    return company_id


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()


def _date_value(value: str, fallback: str = "") -> str:
    raw = str(value or fallback or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Data inválida: {raw}") from exc


def _money_from_cents(value: int | None) -> float | None:
    return None if value is None else round(int(value) / 100, 2)


def _cents(value: float) -> int:
    return int(round(float(value) * 100))
def ensure_chart_of_accounts(agent_id: str) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)

    def mutate(items: list[dict[str, Any]]):
        existing = {
            str(item.get("code") or "")
            for item in items
            if str(item.get("company_id") or "") == company_id
        }
        for code, name, nature, dre_group, cashflow_group in DEFAULT_ACCOUNTS:
            if code in existing:
                continue
            items.append({
                "id": f"{company_id}:{code}",
                "company_id": company_id,
                "code": code,
                "name": name,
                "nature": nature,
                "dre_group": dre_group,
                "cashflow_group": cashflow_group,
                "active": True,
                "created_at": _now(),
                "updated_at": _now(),
            })
        return [
            dict(item) for item in items
            if str(item.get("company_id") or "") == company_id
        ]

    return mutate_collection(ACCOUNT_COLLECTION, mutate)


def list_chart_of_accounts(agent_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    rows = ensure_chart_of_accounts(agent_id)
    if active_only:
        rows = [item for item in rows if bool(item.get("active", True))]
    return sorted(rows, key=lambda item: str(item.get("code") or ""))


def upsert_chart_account(
    agent_id: str,
    *,
    code: str,
    name: str,
    nature: str,
    dre_group: str,
    cashflow_group: str,
    active: bool = True,
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    code = str(code or "").strip()
    if not code or not str(name or "").strip():
        raise ValueError("Código e nome são obrigatórios")

    def mutate(items: list[dict[str, Any]]):
        now = _now()
        row = next((
            item for item in items
            if str(item.get("company_id") or "") == company_id
            and str(item.get("code") or "") == code
        ), None)
        if row is None:
            row = {"id": f"{company_id}:{code}", "company_id": company_id, "created_at": now}
            items.append(row)
        row.update({
            "code": code,
            "name": str(name).strip(),
            "nature": str(nature or "other").strip().lower(),
            "dre_group": str(dre_group or "non_dre").strip().lower(),
            "cashflow_group": str(cashflow_group or "unclassified").strip().lower(),
            "active": bool(active),
            "updated_at": now,
        })
        return dict(row)

    return mutate_collection(ACCOUNT_COLLECTION, mutate)


def _account_map(agent_id: str) -> dict[str, dict[str, Any]]:
    return {str(item["code"]): item for item in list_chart_of_accounts(agent_id, active_only=False)}


def _bank_connect() -> sqlite3.Connection:
    if not BANK_DB_PATH.is_file():
        raise FileNotFoundError(f"Bank Bridge database not found: {BANK_DB_PATH}")
    conn = sqlite3.connect(f"file:{BANK_DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _bank_row(key: str) -> dict[str, Any]:
    with _bank_connect() as conn:
        row = conn.execute(
            """SELECT external_key,posted_at,amount_cents,direction,description,balance_after_cents
               FROM transactions WHERE external_key=? LIMIT 1""",
            (str(key),),
        ).fetchone()
    if row is None:
        raise ValueError("Lançamento bancário não encontrado")
    return dict(row)


def _reconciliation_map(company_id: str) -> dict[str, dict[str, Any]]:
    rows = [
        dict(item) for item in load_collection(RECONCILIATION_COLLECTION)
        if str(item.get("company_id") or "") == company_id
    ]
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        key = str(item.get("bank_transaction_key") or "")
        if key and key not in result:
            result[key] = item
    return result
def list_bank_transactions(
    agent_id: str,
    *,
    date_from: str = "",
    date_to: str = "",
    reconciliation_status: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    reconciliations = _reconciliation_map(company_id)
    clauses: list[str] = []
    params: list[Any] = []
    if date_from:
        clauses.append("posted_at>=?")
        params.append(_date_value(date_from))
    if date_to:
        clauses.append("posted_at<=?")
        params.append(_date_value(date_to))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT external_key,posted_at,amount_cents,direction,description,balance_after_cents "
        f"FROM transactions{where} ORDER BY posted_at DESC,id DESC LIMIT ?"
    )
    params.append(max(1, min(int(limit), 1000)))
    with _bank_connect() as conn:
        raw = [dict(row) for row in conn.execute(sql, params).fetchall()]

    rows: list[dict[str, Any]] = []
    for row in raw:
        rec = reconciliations.get(str(row["external_key"]))
        status = str((rec or {}).get("status") or "unreconciled")
        if reconciliation_status and status != reconciliation_status:
            continue
        rows.append({
            "bank_transaction_key": row["external_key"],
            "date": row["posted_at"],
            "direction": row["direction"],
            "amount": _money_from_cents(row["amount_cents"]),
            "description": row["description"],
            "balance_after": _money_from_cents(row["balance_after_cents"]),
            "reconciliation_status": status,
            "reconciliation": rec,
        })
    return {"source": "unicred-bank-bridge", "read_only": True, "transactions": rows}


def bank_balance(agent_id: str) -> dict[str, Any]:
    _company_for_agent(agent_id)
    with _bank_connect() as conn:
        row = conn.execute(
            """SELECT posted_at,balance_after_cents
               FROM transactions
               WHERE balance_after_cents IS NOT NULL
               ORDER BY posted_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        sync = conn.execute(
            """SELECT finished_at,status,fetched_count,new_count,existing_count,error_code
               FROM sync_runs ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    return {
        "source": "unicred-bank-bridge",
        "as_of": row["posted_at"] if row else None,
        "balance": _money_from_cents(row["balance_after_cents"]) if row else None,
        "last_sync": dict(sync) if sync else None,
    }


def list_payables(
    agent_id: str,
    *,
    status: str = "",
    due_from: str = "",
    due_to: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)
    rows = [
        dict(item) for item in load_collection(PAYABLE_COLLECTION)
        if str(item.get("company_id") or "") == company_id
    ]
    if status:
        rows = [item for item in rows if str(item.get("status") or "") == status]
    if due_from:
        start = _date_value(due_from)
        rows = [item for item in rows if str(item.get("due_date") or "") >= start]
    if due_to:
        end = _date_value(due_to)
        rows = [item for item in rows if str(item.get("due_date") or "") <= end]
    rows.sort(key=lambda item: (str(item.get("due_date") or ""), str(item.get("created_at") or "")))
    return rows[:max(1, min(int(limit), 1000))]
def record_payable(
    agent_id: str,
    *,
    amount: float,
    description: str,
    counterparty: str,
    due_date: str,
    competence_date: str = "",
    account_code: str = "9.9.99",
    document_ref: str = "",
    operation_ref: str = "",
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    accounts = _account_map(agent_id)
    if account_code not in accounts:
        raise ValueError("Conta financeira inexistente no plano de contas")
    if float(amount) <= 0 or not str(description or "").strip():
        raise ValueError("Valor positivo e descrição são obrigatórios")
    payable_id = uuid4().hex
    ledger_id = uuid4().hex
    now = _now()
    due = _date_value(due_date)
    competence = _date_value(competence_date, due)
    payable = {
        "id": payable_id,
        "company_id": company_id,
        "amount": round(float(amount), 2),
        "description": str(description).strip(),
        "counterparty": str(counterparty or "").strip(),
        "due_date": due,
        "competence_date": competence,
        "account_code": account_code,
        "document_ref": str(document_ref or "").strip(),
        "operation_ref": str(operation_ref or "").strip(),
        "status": "open",
        "ledger_event_id": ledger_id,
        "paid_at": "",
        "bank_transaction_key": "",
        "created_at": now,
        "updated_at": now,
    }

    def mutate_payables(items: list[dict[str, Any]]):
        items.append(payable)
        return dict(payable)

    mutate_collection(PAYABLE_COLLECTION, mutate_payables)

    account = accounts[account_code]
    ledger = {
        "id": ledger_id,
        "company_id": company_id,
        "kind": "expense",
        "amount": round(float(amount), 2),
        "description": str(description).strip(),
        "due_date": due,
        "competence_date": competence,
        "payment_date": "",
        "status": "open",
        "category": str(account.get("name") or ""),
        "account_code": account_code,
        "counterparty": str(counterparty or "").strip(),
        "document_ref": str(document_ref or "").strip(),
        "operation_ref": str(operation_ref or "").strip(),
        "source": "accounts_payable",
        "source_ref": payable_id,
        "bank_transaction_key": "",
        "reconciliation_status": "unreconciled",
        "created_at": now,
        "updated_at": now,
    }
    mutate_collection(LEDGER_COLLECTION, lambda items: (items.append(ledger), dict(ledger))[1])
    return payable


def update_payable(
    agent_id: str,
    payable_id: str,
    *,
    status: str,
    paid_at: str = "",
    bank_transaction_key: str = "",
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    normalized = str(status or "").strip().lower()
    if normalized not in {"open", "paid", "cancelled"}:
        raise ValueError("Status de conta a pagar inválido")

    def mutate(items: list[dict[str, Any]]):
        for item in items:
            if item.get("id") != payable_id or str(item.get("company_id") or "") != company_id:
                continue
            item["status"] = normalized
            if paid_at:
                item["paid_at"] = _date_value(paid_at)
            if bank_transaction_key:
                item["bank_transaction_key"] = str(bank_transaction_key)
            item["updated_at"] = _now()
            return dict(item)
        raise ValueError("Conta a pagar não encontrada")

    result = mutate_collection(PAYABLE_COLLECTION, mutate)
    ledger_id = str(result.get("ledger_event_id") or "")

    def mutate_ledger(items: list[dict[str, Any]]):
        for item in items:
            if item.get("id") != ledger_id or str(item.get("company_id") or "") != company_id:
                continue
            item["status"] = normalized
            if paid_at:
                item["payment_date"] = _date_value(paid_at)
            if bank_transaction_key:
                item["bank_transaction_key"] = str(bank_transaction_key)
                item["reconciliation_status"] = "confirmed"
            item["updated_at"] = _now()
            return
    mutate_collection(LEDGER_COLLECTION, mutate_ledger)
    return result
def _upsert_bank_ledger(
    company_id: str,
    bank: dict[str, Any],
    *,
    account: dict[str, Any],
    competence_date: str,
    target_type: str,
    target_ref: str,
) -> str:
    key = str(bank["external_key"])
    ledger_id = f"bank:{key}"
    kind = "income" if bank["direction"] == "credit" else "expense"
    amount = abs(_money_from_cents(bank["amount_cents"]) or 0.0)

    def mutate(items: list[dict[str, Any]]):
        existing = next((
            item for item in items
            if str(item.get("company_id") or "") == company_id
            and str(item.get("bank_transaction_key") or "") == key
        ), None)
        if existing is None:
            existing = {
                "id": ledger_id,
                "company_id": company_id,
                "created_at": _now(),
            }
            items.append(existing)
        existing.update({
            "kind": kind,
            "amount": amount,
            "description": str(bank.get("description") or ""),
            "due_date": str(bank.get("posted_at") or ""),
            "competence_date": competence_date,
            "payment_date": str(bank.get("posted_at") or ""),
            "status": "paid",
            "category": str(account.get("name") or ""),
            "account_code": str(account.get("code") or ""),
            "counterparty": "",
            "document_ref": "",
            "operation_ref": target_ref if target_type == "operation" else "",
            "source": "unicred",
            "source_ref": key,
            "bank_transaction_key": key,
            "reconciliation_status": "confirmed",
            "target_type": target_type,
            "target_ref": target_ref,
            "updated_at": _now(),
        })
        return str(existing["id"])

    return mutate_collection(LEDGER_COLLECTION, mutate)


def reconcile_bank_transaction(
    agent_id: str,
    *,
    bank_transaction_key: str,
    account_code: str,
    target_type: str = "other",
    target_ref: str = "",
    competence_date: str = "",
    notes: str = "",
    human_confirmed: bool = False,
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    bank = _bank_row(bank_transaction_key)
    accounts = _account_map(agent_id)
    if account_code not in accounts:
        raise ValueError("Conta financeira inexistente no plano de contas")
    status = "confirmed" if human_confirmed else "proposed"
    competence = _date_value(competence_date, str(bank["posted_at"]))
    target_type = str(target_type or "other").strip().lower()
    now = _now()

    def mutate(items: list[dict[str, Any]]):
        row = next((
            item for item in items
            if str(item.get("company_id") or "") == company_id
            and str(item.get("bank_transaction_key") or "") == str(bank_transaction_key)
        ), None)
        if row is None:
            row = {
                "id": uuid4().hex,
                "company_id": company_id,
                "bank_transaction_key": str(bank_transaction_key),
                "created_at": now,
            }
            items.append(row)
        row.update({
            "status": status,
            "account_code": account_code,
            "target_type": target_type,
            "target_ref": str(target_ref or "").strip(),
            "competence_date": competence,
            "notes": str(notes or "").strip(),
            "bank_date": str(bank["posted_at"]),
            "direction": str(bank["direction"]),
            "amount": _money_from_cents(bank["amount_cents"]),
            "updated_at": now,
        })
        return dict(row)

    reconciliation = mutate_collection(RECONCILIATION_COLLECTION, mutate)
    if not human_confirmed:
        return reconciliation

    if target_type == "payable" and target_ref:
        update_payable(
            agent_id,
            str(target_ref),
            status="paid",
            paid_at=str(bank["posted_at"]),
            bank_transaction_key=str(bank_transaction_key),
        )
    elif target_type == "ledger" and target_ref:
        mark_ledger_paid_from_bank(
            agent_id,
            ledger_event_id=str(target_ref),
            bank_transaction_key=str(bank_transaction_key),
            account_code=account_code,
        )
    else:
        _upsert_bank_ledger(
            company_id,
            bank,
            account=accounts[account_code],
            competence_date=competence,
            target_type=target_type,
            target_ref=str(target_ref or ""),
        )
    return reconciliation
def reconciliation_report(
    agent_id: str,
    *,
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    all_rows = list_bank_transactions(
        agent_id, date_from=date_from, date_to=date_to, limit=1000,
    )["transactions"]
    counts = {"confirmed": 0, "proposed": 0, "unreconciled": 0}
    value = {
        "confirmed": {"credits": 0.0, "debits": 0.0},
        "proposed": {"credits": 0.0, "debits": 0.0},
        "unreconciled": {"credits": 0.0, "debits": 0.0},
    }
    for item in all_rows:
        status = str(item.get("reconciliation_status") or "unreconciled")
        if status not in counts:
            status = "unreconciled"
        counts[status] += 1
        side = "credits" if item.get("direction") == "credit" else "debits"
        value[status][side] = round(value[status][side] + abs(float(item.get("amount") or 0)), 2)
    return {
        "source": "unicred-bank-bridge",
        "period": {"from": date_from or None, "to": date_to or None},
        "total_transactions": len(all_rows),
        "counts": counts,
        "amounts": value,
        "complete": counts["unreconciled"] == 0 and counts["proposed"] == 0,
    }


def reconciliation_workspace(
    agent_id: str,
    *,
    date_from: str = "",
    date_to: str = "",
    official_receivables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bank = list_bank_transactions(
        agent_id,
        date_from=date_from,
        date_to=date_to,
        reconciliation_status="unreconciled",
        limit=500,
    )["transactions"]
    payables = list_payables(agent_id, status="open", limit=500)
    receivables = list((official_receivables or {}).get("itens") or [])
    already_received = confirmed_target_refs(agent_id, "receivable")
    open_receivables = [
        item for item in receivables
        if str(item.get("status_categoria") or "") in {"a_vencer", "vence_hoje", "vencido"}
        and str(item.get("id") or "") not in already_received
    ]
    suggestions: list[dict[str, Any]] = []
    for tx in bank:
        amount_cents = _cents(abs(float(tx.get("amount") or 0)))
        candidates: list[dict[str, Any]] = []
        if tx.get("direction") == "credit":
            for item in open_receivables:
                if _cents(float(item.get("valor") or 0)) != amount_cents:
                    continue
                score = "exact_amount"
                customer = str(item.get("cliente_nome") or "")
                if customer and _norm(customer) in _norm(str(tx.get("description") or "")):
                    score = "exact_amount_and_name"
                candidates.append({
                    "target_type": "receivable",
                    "target_ref": item.get("id"),
                    "order_ref": item.get("numero_pedido"),
                    "counterparty": customer,
                    "amount": item.get("valor"),
                    "match": score,
                })
        else:
            for item in payables:
                if _cents(float(item.get("amount") or 0)) != amount_cents:
                    continue
                score = "exact_amount"
                counterparty = str(item.get("counterparty") or "")
                if counterparty and _norm(counterparty) in _norm(str(tx.get("description") or "")):
                    score = "exact_amount_and_name"
                candidates.append({
                    "target_type": "payable",
                    "target_ref": item.get("id"),
                    "counterparty": counterparty,
                    "amount": item.get("amount"),
                    "match": score,
                })
        suggestions.append({
            "bank_transaction": tx,
            "candidates": candidates,
            "unique_exact_candidate": candidates[0] if len(candidates) == 1 else None,
        })
    return {
        "bank_unreconciled": len(bank),
        "workspace": suggestions,
        "rule": "suggestions_only_human_confirms",
    }
def _ledger_rows(agent_id: str) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)
    return [
        dict(item) for item in load_collection(LEDGER_COLLECTION)
        if str(item.get("company_id") or "") == company_id
    ]


def dre_report(agent_id: str, *, date_from: str, date_to: str) -> dict[str, Any]:
    start = _date_value(date_from)
    end = _date_value(date_to)
    if start > end:
        raise ValueError("Período da DRE inválido")
    accounts = _account_map(agent_id)
    rows = []
    unclassified: list[dict[str, Any]] = []
    for item in _ledger_rows(agent_id):
        if str(item.get("status") or "") == "cancelled":
            continue
        competence = _date_value(
            str(item.get("competence_date") or ""),
            str(item.get("payment_date") or item.get("due_date") or ""),
        )
        if not competence or competence < start or competence > end:
            continue
        code = str(item.get("account_code") or "")
        account = accounts.get(code)
        if account is None or str(account.get("dre_group") or "") == "non_dre":
            unclassified.append(item)
            continue
        rows.append((item, account, competence))

    totals = {
        "revenue": 0.0,
        "cogs": 0.0,
        "opex": 0.0,
        "financial": 0.0,
        "taxes": 0.0,
    }
    for item, account, _ in rows:
        group = str(account.get("dre_group") or "")
        if group in totals:
            totals[group] = round(totals[group] + abs(float(item.get("amount") or 0)), 2)

    gross_profit = round(totals["revenue"] - totals["cogs"], 2)
    operating_result = round(gross_profit - totals["opex"], 2)
    net_result = round(operating_result - totals["financial"] - totals["taxes"], 2)
    reconciliation = reconciliation_report(agent_id, date_from=start, date_to=end)
    reconciliation_complete = bool(reconciliation.get("complete"))
    report_complete = len(unclassified) == 0 and reconciliation_complete and bool(rows)
    return {
        "report": "management_dre",
        "basis": "competence_date_from_finance_ledger",
        "period": {"from": start, "to": end},
        "revenue": totals["revenue"],
        "cogs": totals["cogs"],
        "gross_profit": gross_profit,
        "operating_expenses": totals["opex"],
        "operating_result": operating_result,
        "financial_expenses": totals["financial"],
        "taxes": totals["taxes"],
        "net_result": net_result,
        "classified_entries": len(rows),
        "excluded_or_unclassified_entries": len(unclassified),
        "reconciliation": reconciliation,
        "complete": report_complete,
        "warning": (
            None if report_complete
            else "DRE parcial: conclua a conciliação/classificação e garanta lançamentos por competência."
        ),
    }


def cashflow_report(
    agent_id: str,
    *,
    as_of: str = "",
    forecast_days: int = 30,
    official_receivables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = _date_value(as_of, date.today().isoformat())
    horizon = (date.fromisoformat(target) + timedelta(days=max(0, min(int(forecast_days), 365)))).isoformat()
    balance = bank_balance(agent_id)
    bank_month_start = date.fromisoformat(target).replace(day=1).isoformat()
    bank_rows = list_bank_transactions(
        agent_id, date_from=bank_month_start, date_to=target, limit=1000,
    )["transactions"]
    realized_in = round(sum(
        abs(float(item.get("amount") or 0)) for item in bank_rows if item.get("direction") == "credit"
    ), 2)
    realized_out = round(sum(
        abs(float(item.get("amount") or 0)) for item in bank_rows if item.get("direction") == "debit"
    ), 2)

    receivables_payload = official_receivables or {}
    already_received = confirmed_target_refs(agent_id, "receivable")
    forecast_in = 0.0
    receivable_rows = []
    for item in list(receivables_payload.get("itens") or []):
        due = str(item.get("vencimento") or "")
        if not due or due > horizon:
            continue
        if str(item.get("status_categoria") or "") not in {"a_vencer", "vence_hoje", "vencido"}:
            continue
        if str(item.get("id") or "") in already_received:
            continue
        forecast_in = round(forecast_in + float(item.get("valor") or 0), 2)
        receivable_rows.append(item)

    payable_rows = [
        item for item in list_payables(agent_id, status="open", limit=1000)
        if str(item.get("due_date") or "") <= horizon
    ]
    payable_signatures = {
        (
            str(item.get("due_date") or ""),
            _cents(float(item.get("amount") or 0)),
            _norm(str(item.get("counterparty") or "")),
        )
        for item in payable_rows
    }
    inactive_dda_statuses = {"PAGO", "LIQUIDADO", "BAIXADO", "CANCELADO"}
    dda_candidates = [
        item for item in list_dda_bills(agent_id, date_to=horizon, limit=10000)
        if str(item.get("dda_status") or "").upper() not in inactive_dda_statuses
    ]
    dda_unmatched = [
        item for item in dda_candidates
        if (
            str(item.get("due_date") or ""),
            _cents(float(item.get("amount") or 0)),
            _norm(str(item.get("counterparty") or "")),
        ) not in payable_signatures
    ]
    ledger_payables_total = round(sum(float(item.get("amount") or 0) for item in payable_rows), 2)
    dda_unclassified_total = round(sum(float(item.get("amount") or 0) for item in dda_unmatched), 2)
    forecast_out = round(ledger_payables_total + dda_unclassified_total, 2)
    current_balance = balance.get("balance")
    projected = None if current_balance is None else round(float(current_balance) + forecast_in - forecast_out, 2)
    return {
        "report": "cashflow",
        "as_of": target,
        "forecast_to": horizon,
        "bank_balance": current_balance,
        "bank_balance_as_of": balance.get("as_of"),
        "realized_month": {
            "inflows": realized_in,
            "outflows": realized_out,
            "net": round(realized_in - realized_out, 2),
        },
        "forecast": {
            "receivables": forecast_in,
            "payables": forecast_out,
            "ledger_payables": ledger_payables_total,
            "dda_unclassified": dda_unclassified_total,
            "net": round(forecast_in - forecast_out, 2),
            "projected_bank_balance": projected,
        },
        "coverage": {
            "receivables_source_ok": bool(receivables_payload.get("ok")),
            "receivables_count": len(receivable_rows),
            "payables_count": len(payable_rows),
            "dda_unclassified_count": len(dda_unmatched),
            "reconciliation": reconciliation_report(agent_id, date_from=bank_month_start, date_to=target),
        },
    }


def financial_position_report(
    agent_id: str,
    *,
    official_receivables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receivables = official_receivables or {}
    already_received = confirmed_target_refs(agent_id, "receivable")
    adjusted_receivables = [
        item for item in list(receivables.get("itens") or [])
        if str(item.get("status_categoria") or "") in {"a_vencer", "vence_hoje", "vencido"}
        and str(item.get("id") or "") not in already_received
    ]
    adjusted_receivable_total = round(
        sum(float(item.get("valor") or 0) for item in adjusted_receivables), 2
    )
    payables = list_payables(agent_id, status="open", limit=1000)
    payable_total = round(sum(float(item.get("amount") or 0) for item in payables), 2)
    payable_signatures = {
        (
            str(item.get("due_date") or ""),
            _cents(float(item.get("amount") or 0)),
            _norm(str(item.get("counterparty") or "")),
        )
        for item in payables
    }
    inactive_dda_statuses = {"PAGO", "LIQUIDADO", "BAIXADO", "CANCELADO"}
    dda_open = [
        item for item in list_dda_bills(agent_id, limit=10000)
        if str(item.get("dda_status") or "").upper() not in inactive_dda_statuses
        and (
            str(item.get("due_date") or ""),
            _cents(float(item.get("amount") or 0)),
            _norm(str(item.get("counterparty") or "")),
        ) not in payable_signatures
    ]
    dda_total = round(sum(float(item.get("amount") or 0) for item in dda_open), 2)
    return {
        "report": "financial_position",
        "generated_at": _now(),
        "bank": bank_balance(agent_id),
        "receivables": {
            "source_ok": bool(receivables.get("ok")),
            "official_summary": dict(receivables.get("resumo") or {}),
            "official_count": len(list(receivables.get("itens") or [])),
            "adjusted_open_total": adjusted_receivable_total,
            "adjusted_open_count": len(adjusted_receivables),
        },
        "payables": {
            "open_total": payable_total,
            "open_count": len(payables),
            "dda_unclassified_total": dda_total,
            "dda_unclassified_count": len(dda_open),
            "expected_outflows_total": round(payable_total + dda_total, 2),
        },
        "reconciliation": reconciliation_report(agent_id),
    }


def record_accrual(
    agent_id: str,
    *,
    kind: str,
    amount: float,
    description: str,
    competence_date: str,
    account_code: str,
    due_date: str = "",
    counterparty: str = "",
    document_ref: str = "",
    operation_ref: str = "",
    source: str = "manual",
    source_ref: str = "",
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    accounts = _account_map(agent_id)
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"income", "expense"}:
        raise ValueError("kind deve ser income ou expense")
    if account_code not in accounts:
        raise ValueError("Conta financeira inexistente no plano de contas")
    if float(amount) <= 0 or not str(description or "").strip():
        raise ValueError("Valor positivo e descrição são obrigatórios")
    now = _now()
    entry = {
        "id": uuid4().hex,
        "company_id": company_id,
        "kind": normalized_kind,
        "amount": round(float(amount), 2),
        "description": str(description).strip(),
        "due_date": _date_value(due_date) if due_date else "",
        "competence_date": _date_value(competence_date),
        "payment_date": "",
        "status": "open",
        "category": str(accounts[account_code].get("name") or ""),
        "account_code": account_code,
        "counterparty": str(counterparty or "").strip(),
        "document_ref": str(document_ref or "").strip(),
        "operation_ref": str(operation_ref or "").strip(),
        "source": str(source or "manual").strip(),
        "source_ref": str(source_ref or "").strip(),
        "bank_transaction_key": "",
        "reconciliation_status": "unreconciled",
        "created_at": now,
        "updated_at": now,
    }
    mutate_collection(LEDGER_COLLECTION, lambda items: (items.append(entry), dict(entry))[1])
    return entry


def mark_ledger_paid_from_bank(
    agent_id: str,
    *,
    ledger_event_id: str,
    bank_transaction_key: str,
    account_code: str,
) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    bank = _bank_row(bank_transaction_key)

    def mutate(items: list[dict[str, Any]]):
        entry = next((
            item for item in items
            if str(item.get("company_id") or "") == company_id
            and str(item.get("id") or "") == str(ledger_event_id)
        ), None)
        if entry is None:
            raise ValueError("Lançamento de competência não encontrado")
        expected_kind = "income" if bank["direction"] == "credit" else "expense"
        if str(entry.get("kind") or "") != expected_kind:
            raise ValueError("Direção bancária incompatível com o lançamento de competência")
        if _cents(float(entry.get("amount") or 0)) != abs(int(bank["amount_cents"])):
            raise ValueError("Valor bancário diferente do lançamento de competência")
        if str(entry.get("account_code") or "") != str(account_code):
            raise ValueError("Conta gerencial diferente do lançamento de competência")
        entry["status"] = "paid"
        entry["payment_date"] = str(bank["posted_at"])
        entry["bank_transaction_key"] = str(bank_transaction_key)
        entry["reconciliation_status"] = "confirmed"
        entry["updated_at"] = _now()
        return dict(entry)

    return mutate_collection(LEDGER_COLLECTION, mutate)


def confirmed_target_refs(agent_id: str, target_type: str) -> set[str]:
    company_id = _company_for_agent(agent_id)
    return {
        str(item.get("target_ref") or "")
        for item in load_collection(RECONCILIATION_COLLECTION)
        if str(item.get("company_id") or "") == company_id
        and str(item.get("status") or "") == "confirmed"
        and str(item.get("target_type") or "") == str(target_type)
        and str(item.get("target_ref") or "")
    }


def list_dda_bills(
    agent_id: str,
    *,
    date_from: str = "",
    date_to: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    _company_for_agent(agent_id)
    clauses: list[str] = []
    params: list[Any] = []
    if date_from:
        clauses.append("due_date>=?")
        params.append(_date_value(date_from))
    if date_to:
        clauses.append("due_date<=?")
        params.append(_date_value(date_to))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT external_key,due_date,amount_cents,beneficiary_name,"
        "title_reference,status,last_seen_at "
        f"FROM dda_bills{where} ORDER BY due_date ASC,id ASC LIMIT ?"
    )
    params.append(max(1, min(int(limit), 10000)))
    try:
        with _bank_connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return [
        {
            "id": f"dda:{row['external_key']}",
            "bank_title_key": row["external_key"],
            "source": "unicred_dda",
            "due_date": row["due_date"],
            "amount": _money_from_cents(row["amount_cents"]),
            "counterparty": row["beneficiary_name"],
            "description": "Boleto DDA",
            "document_ref": row["title_reference"] or "",
            "dda_status": row["status"] or "",
            "classification_status": "unclassified",
            "last_seen_at": row["last_seen_at"],
        }
        for row in rows
    ]
