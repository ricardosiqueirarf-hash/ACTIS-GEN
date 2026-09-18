"""Local-only ColorGlass Finance ERP dashboard."""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from meuharness.domains.colorglass_finance import official_order, official_receivables
from meuharness.domains.colorglass_finance_reporting import (
    bank_balance,
    cashflow_report,
    dre_report,
    financial_position_report,
    list_bank_transactions,
    list_chart_of_accounts,
    list_dda_bills,
    list_payables,
    reconciliation_report,
    reconcile_bank_transaction,
    upsert_chart_account,
)

AGENT_ID = "financeiro-colorglass"
ASSETS = Path(__file__).with_name("web_assets")
INDEX = ASSETS / "finance-erp.html"
CSS = ASSETS / "finance-erp.css"
JS = ASSETS / "finance-erp.js"

_RECEIVABLES_LOCK = threading.Lock()
_RECEIVABLES_CACHE: dict = {
    "ok": False,
    "status": "not_loaded",
    "itens": [],
    "resumo": {},
}


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _receivables_snapshot() -> dict:
    with _RECEIVABLES_LOCK:
        return json.loads(json.dumps(_RECEIVABLES_CACHE, ensure_ascii=False, default=str))


def _refresh_receivables(timeout: float = 5.0) -> dict:
    global _RECEIVABLES_CACHE
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(official_receivables, AGENT_ID, period="aberto")
    try:
        result = future.result(timeout=timeout)
        if not isinstance(result, dict):
            result = {"ok": False, "status": "invalid_response", "itens": [], "resumo": {}}
    except FutureTimeout:
        result = {"ok": False, "status": "timeout", "itens": [], "resumo": {}}
        future.cancel()
    except Exception as exc:
        result = {"ok": False, "status": "error", "error": str(exc), "itens": [], "resumo": {}}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    with _RECEIVABLES_LOCK:
        _RECEIVABLES_CACHE = result
    return _receivables_snapshot()


def _assistant_state(params: dict[str, list[str]] | None = None) -> dict:
    params = params or {}
    default_from, default_to = _month_range()
    date_from = (params.get("from") or [default_from])[0] or default_from
    date_to = (params.get("to") or [default_to])[0] or default_to
    pending = list_bank_transactions(
        AGENT_ID,
        date_from=date_from,
        date_to=date_to,
        reconciliation_status="unreconciled",
        limit=500,
    )["transactions"]
    accounts = list_chart_of_accounts(AGENT_ID)
    state_rows = [
        {
            "key": str(item.get("bank_transaction_key") or ""),
            "status": str(item.get("reconciliation_status") or ""),
            "updated_at": str((item.get("reconciliation") or {}).get("updated_at") or ""),
        }
        for item in pending
    ]
    state_hash_source = json.dumps(
        {"pending": state_rows, "accounts": accounts},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "ok": True,
        "period": {"from": date_from, "to": date_to},
        "count": len(pending),
        "pending": pending,
        "chart_of_accounts": accounts,
        "state_hash": hashlib.sha256(state_hash_source.encode("utf-8")).hexdigest(),
        "updated_at": max(
            (str((item.get("reconciliation") or {}).get("updated_at") or "") for item in pending),
            default="",
        ),
    }


def _month_range() -> tuple[str, str]:
    today = date.today()
    start = today.replace(day=1).isoformat()
    if today.month == 12:
        nxt = today.replace(year=today.year + 1, month=1, day=1)
    else:
        nxt = today.replace(month=today.month + 1, day=1)
    end = date.fromordinal(nxt.toordinal() - 1).isoformat()
    return start, end


def _state(params: dict[str, list[str]]) -> dict:
    default_from, default_to = _month_range()
    date_from = (params.get("from") or [default_from])[0] or default_from
    date_to = (params.get("to") or [default_to])[0] or default_to
    forecast_days = int((params.get("forecast_days") or ["30"])[0] or 30)
    receivables = _receivables_snapshot()
    usable_receivables = receivables if receivables.get("ok") else None

    bank = bank_balance(AGENT_ID)
    rec = reconciliation_report(AGENT_ID, date_from=date_from, date_to=date_to)
    transactions = list_bank_transactions(
        AGENT_ID, date_from=date_from, date_to=date_to, limit=500,
    )["transactions"]
    payables = list_payables(AGENT_ID, limit=1000)
    dda_bills_all = list_dda_bills(AGENT_ID, limit=10000)
    dda_bills = [
        item for item in dda_bills_all
        if (not date_from or str(item.get("due_date") or "") >= date_from)
        and (not date_to or str(item.get("due_date") or "") <= date_to)
    ]
    chart = list_chart_of_accounts(AGENT_ID)
    cashflow = cashflow_report(
        AGENT_ID,
        as_of=min(date.today().isoformat(), date_to),
        forecast_days=forecast_days,
        official_receivables=usable_receivables,
    )
    dre = dre_report(AGENT_ID, date_from=date_from, date_to=date_to)
    position = financial_position_report(
        AGENT_ID,
        official_receivables=usable_receivables,
    )
    return {
        "ok": True,
        "period": {"from": date_from, "to": date_to, "forecast_days": forecast_days},
        "bank": bank,
        "reconciliation": rec,
        "transactions": transactions,
        "payables": payables,
        "dda_bills": dda_bills,
        "dda_bills_all_count": len(dda_bills_all),
        "dda_bills_all_total": round(sum(float(item.get("amount") or 0) for item in dda_bills_all), 2),
        "chart_of_accounts": chart,
        "cashflow": cashflow,
        "dre": dre,
        "position": position,
        "receivables": receivables,
    }


class FinanceERPHandler(BaseHTTPRequestHandler):
    server_version = "ACTIS-Finance-ERP/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/financeiro"}:
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
                return
            if parsed.path == "/finance-erp.css":
                self._send(200, CSS.read_bytes(), "text/css; charset=utf-8")
                return
            if parsed.path == "/finance-erp.js":
                self._send(200, JS.read_bytes(), "text/javascript; charset=utf-8")
                return
            if parsed.path == "/api/state":
                self._json(200, _state(parse_qs(parsed.query)))
                return
            if parsed.path == "/api/receivables":
                self._json(200, _receivables_snapshot())
                return
            if parsed.path == "/api/assistant/pending":
                self._json(200, _assistant_state(parse_qs(parsed.query)))
                return
            if parsed.path == "/health":
                self._json(200, {"ok": True, "service": "finance-erp", "local_only": True})
                return
            self._json(404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/receivables/refresh":
                self._json(200, _refresh_receivables())
                return
            if parsed.path == "/api/chart/account":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                result = upsert_chart_account(
                    AGENT_ID,
                    code=str(payload.get("code") or ""),
                    name=str(payload.get("name") or ""),
                    nature=str(payload.get("nature") or "other"),
                    dre_group=str(payload.get("dre_group") or "non_dre"),
                    cashflow_group=str(payload.get("cashflow_group") or "unclassified"),
                    active=True,
                )
                self._json(200, {"ok": True, "account": result})
                return
            if parsed.path == "/api/reconciliation/confirm":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                account_code = str(payload.get("account_code") or "").strip()
                accounts = {str(item.get("code") or ""): item for item in list_chart_of_accounts(AGENT_ID, active_only=False)}
                account = accounts.get(account_code)
                if account is None:
                    raise ValueError("Conta financeira inexistente no plano de contas")
                order_ref = str(payload.get("order_ref") or "").strip()
                order = None
                if (
                    str(account.get("dre_group") or "").strip().lower() == "revenue"
                    or str(account.get("nature") or "").strip().lower() == "income"
                ):
                    if not order_ref:
                        raise ValueError("Receita de vendas exige o ID do pedido")
                    order = official_order(AGENT_ID, order_ref)
                    if order.get("ok"):
                        order_ref = str(order.get("id") or order_ref)
                    else:
                        order = {**dict(order or {}), "provided_ref": order_ref, "verified": False}
                result = reconcile_bank_transaction(
                    AGENT_ID,
                    bank_transaction_key=str(payload.get("bank_transaction_key") or ""),
                    account_code=account_code,
                    target_type=str(payload.get("target_type") or "other"),
                    target_ref=str(payload.get("target_ref") or ""),
                    order_ref=order_ref,
                    competence_date=str(payload.get("competence_date") or ""),
                    notes=str(payload.get("notes") or ""),
                    human_confirmed=True,
                )
                self._json(200, {"ok": True, "reconciliation": result, "order": order})
                return
            self._json(404, {"ok": False, "error": "not_found"})
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Finance ERP must bind to localhost only")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FinanceERPHandler)
    print(f"ColorGlass Finance ERP: http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
