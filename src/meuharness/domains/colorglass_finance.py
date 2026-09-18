"""ColorGlass finance ledger and operational daily reporting."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from meuharness.agents import get_agent, list_agents
from meuharness.domains.colorglass_finance_reporting import (
    bank_balance,
    cashflow_report,
    dre_report,
    financial_position_report,
    list_bank_transactions,
    list_chart_of_accounts,
    list_payables,
    record_payable,
    reconciliation_report,
    reconciliation_workspace,
    reconcile_bank_transaction,
    update_payable,
    upsert_chart_account,
)
from meuharness.storage import load_collection, save_collection

COLLECTION = "colorglass_cash_events"
KINDS = {"income", "expense"}


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


def _amount(value: float) -> float:
    amount = round(float(value), 2)
    if amount < 0:
        raise ValueError("Valor financeiro não pode ser negativo")

    return amount


def list_cash_events(agent_id: str, *, status: str = "", limit: int = 200) -> list[dict[str, Any]]:
    company_id = _company_for_agent(agent_id)
    rows = [
        dict(item) for item in load_collection(COLLECTION)
        if str(item.get("company_id") or "") == company_id
    ]
    if status:
        rows = [item for item in rows if str(item.get("status") or "") == status]
    rows.sort(key=lambda item: (str(item.get("due_date") or ""), str(item.get("updated_at") or "")), reverse=True)
    return rows[:max(1, min(int(limit), 1000))]


def record_cash_event(agent_id: str, *, kind: str, amount: float, description: str,
                      due_date: str = "", status: str = "open", category: str = "",
                      counterparty: str = "", document_ref: str = "",
                      operation_ref: str = "") -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in KINDS:
        raise ValueError("kind deve ser income ou expense")
    if not str(description or "").strip():
        raise ValueError("Descrição financeira é obrigatória")
    now = _now()
    event = {
        "id": uuid4().hex, "company_id": company_id, "kind": normalized_kind,
        "amount": _amount(amount), "description": str(description).strip(),

        "due_date": str(due_date or "").strip(), "status": str(status or "open").strip().lower(),
        "category": str(category or "").strip(), "counterparty": str(counterparty or "").strip(),
        "document_ref": str(document_ref or "").strip(), "operation_ref": str(operation_ref or "").strip(),
        "created_at": now, "updated_at": now,
    }
    items = load_collection(COLLECTION)
    items.append(event)
    save_collection(COLLECTION, items)
    return dict(event)


def mark_cash_event(agent_id: str, event_id: str, status: str) -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    items = load_collection(COLLECTION)
    for item in items:
        if item.get("id") != event_id or str(item.get("company_id") or "") != company_id:
            continue
        item["status"] = str(status or "").strip().lower()
        item["updated_at"] = _now()
        save_collection(COLLECTION, items)
        return dict(item)
    raise ValueError("Lançamento financeiro não encontrado")


def daily_report(agent_id: str, report_date: str = "") -> dict[str, Any]:
    target = str(report_date or date.today().isoformat())
    events = list_cash_events(agent_id, limit=1000)
    due = [item for item in events if item.get("due_date") == target and item.get("status") not in {"paid", "cancelled"}]
    paid = [item for item in events if item.get("status") == "paid" and str(item.get("updated_at") or "")[:10] == target]

    income_paid = sum(float(item.get("amount") or 0) for item in paid if item.get("kind") == "income")
    expense_paid = sum(float(item.get("amount") or 0) for item in paid if item.get("kind") == "expense")
    from meuharness.domains.colorglass_operations import list_operations
    operations = list_operations(agent_id, limit=500)
    receivable = sum(float((item.get("finance") or {}).get("receivable") or 0) for item in operations)
    return {
        "date": target,
        "cash_in": round(income_paid, 2),
        "cash_out": round(expense_paid, 2),
        "net_cash": round(income_paid - expense_paid, 2),
        "open_receivables": round(receivable, 2),
        "due_today": due,
        "paid_today": paid,
        "open_events": sum(1 for item in events if item.get("status") not in {"paid", "cancelled"}),
    }


def official_receivables(agent_id: str, *, period: str = "aberto", search: str = "") -> dict[str, Any]:
    company_id = _company_for_agent(agent_id)
    session_agent = next((
        item for item in list_agents()
        if str(item.get("company_id") or "") == company_id
        and "colorglass.quotation" in list(item.get("skills") or [])
    ), None)
    if not session_agent:
        return {"ok": False, "status": "session_provider_not_found"}
    from meuharness.colorglass_quotation_tools import _eval
    query = json.dumps({"period": str(period or "aberto"), "search": str(search or "")}, ensure_ascii=False)
    expr = """(async()=>{const q=%s;const token=localStorage.getItem('USER_TOKEN')||localStorage.getItem('ADMIN_TOKEN')||'';if(!token)return {ok:false,status:'auth_required'};const api=(typeof API_BASE!=='undefined'&&API_BASE)||'https://colorglass.onrender.com';const url=api+'/api/financeiro/a-receber?periodo='+encodeURIComponent(q.period)+'&q='+encodeURIComponent(q.search);const r=await fetch(url,{headers:{Authorization:'Bearer '+token}});const d=await r.json().catch(()=>({}));if(r.status===401||r.status===403)return {ok:false,status:'auth_required',http_status:r.status};return {ok:r.ok,status:r.status,success:d.success,escopo:d.escopo,lojaid:d.lojaid,resumo:d.resumo||{},itens:d.itens||[],total_filtrado:d.total_filtrado||0};})()""" % query
    result = _eval(str(session_agent["id"]), expr, await_promise=True)
    return dict(result) if isinstance(result, dict) else {"ok": False, "status": "invalid_response"}


def update_operation_finance(agent_id: str, operation_ref: str, *, total: float | None = None,
                             paid: float | None = None, due_date: str = "",
                             boleto_status: str = "") -> dict[str, Any]:
    from meuharness.domains.colorglass_operations import get_operation, patch_department
    operation = get_operation(agent_id, operation_ref)
    current = dict(operation.get("finance") or {})
    new_total = _amount(total) if total is not None else float(current.get("total") or 0)
    new_paid = _amount(paid) if paid is not None else float(current.get("paid") or 0)

    receivable = max(0.0, round(new_total - new_paid, 2))
    status = "paid" if new_total > 0 and receivable == 0 else "partial" if new_paid > 0 else "open"
    patch: dict[str, Any] = {
        "status": status, "total": new_total, "paid": new_paid, "receivable": receivable,
    }
    if due_date:
        patch["due_date"] = due_date
    if boleto_status:
        patch["boleto_status"] = boleto_status
    return patch_department(agent_id, operation["id"], "finance", patch)


def build_finance_tools(agent_id: str) -> list[object]:
    def finance_daily_report(report_date: str = "") -> str:
        """Gere o resumo diário do caixa, vencimentos e recebíveis registrados."""
        return json.dumps(daily_report(agent_id, report_date), ensure_ascii=False, default=str)

    def finance_official_receivables(period: str = "aberto", search: str = "") -> str:
        """Leia recebíveis/boletos oficiais do ERP ColorGlass em modo somente leitura."""
        return json.dumps(official_receivables(agent_id, period=period, search=search),
                          ensure_ascii=False, default=str)

    def finance_list_events(status: str = "", limit: int = 200) -> str:
        """Liste contas e lançamentos locais de consolidação financeira."""
        return json.dumps({"events": list_cash_events(agent_id, status=status, limit=limit)},
                          ensure_ascii=False, default=str)

    def finance_record_event(kind: str, amount: float, description: str, due_date: str = "",
                             status: str = "open", category: str = "", counterparty: str = "",
                             document_ref: str = "", operation_ref: str = "") -> str:
        """Registre um lançamento financeiro; não executa pagamento nem movimenta banco."""
        return json.dumps(record_cash_event(
            agent_id, kind=kind, amount=amount, description=description, due_date=due_date,
            status=status, category=category, counterparty=counterparty,
            document_ref=document_ref, operation_ref=operation_ref,
        ), ensure_ascii=False, default=str)

    def finance_mark_event(event_id: str, status: str) -> str:
        """Atualize o status de um lançamento, por exemplo paid/cancelled/open."""
        return json.dumps(mark_cash_event(agent_id, event_id, status), ensure_ascii=False, default=str)

    def finance_update_order(operation_ref: str, total: float = -1, paid: float = -1,
                             due_date: str = "", boleto_status: str = "") -> str:
        """Atualize total/pago/recebível e boleto do pedido sem executar movimentação bancária."""
        return json.dumps(update_operation_finance(
            agent_id, operation_ref,
            total=None if total < 0 else total, paid=None if paid < 0 else paid,
            due_date=due_date, boleto_status=boleto_status,
        ), ensure_ascii=False, default=str)

    return [finance_daily_report, finance_official_receivables, finance_list_events,
            finance_record_event, finance_mark_event, finance_update_order]


# Extended controller-grade finance tools. Keep the legacy toolset intact and add
# reporting/reconciliation capabilities without changing existing call contracts.
_build_finance_tools_legacy = build_finance_tools


def build_finance_tools(agent_id: str) -> list[object]:
    tools = list(_build_finance_tools_legacy(agent_id))

    def finance_bank_balance() -> str:
        """Leia o último saldo confirmado no Bank Bridge em modo somente leitura."""
        return json.dumps(bank_balance(agent_id), ensure_ascii=False, default=str)

    def finance_bank_transactions(date_from: str = "", date_to: str = "",
                                  reconciliation_status: str = "unreconciled",
                                  limit: int = 200) -> str:
        """Liste lançamentos bancários normalizados e seu estado de conciliação."""
        return json.dumps(list_bank_transactions(
            agent_id,
            date_from=date_from,
            date_to=date_to,
            reconciliation_status=reconciliation_status,
            limit=limit,
        ), ensure_ascii=False, default=str)

    def finance_reconciliation_workspace(date_from: str = "", date_to: str = "") -> str:
        """Monte a sessão diária com banco pendente, recebíveis, contas a pagar e candidatos."""
        receivables = official_receivables(agent_id, period="aberto")
        return json.dumps(reconciliation_workspace(
            agent_id,
            date_from=date_from,
            date_to=date_to,
            official_receivables=receivables if receivables.get("ok") else None,
        ), ensure_ascii=False, default=str)

    def finance_reconcile_transaction(bank_transaction_key: str, account_code: str,
                                      target_type: str = "other", target_ref: str = "",
                                      competence_date: str = "", notes: str = "",
                                      human_confirmed: bool = False) -> str:
        """Proponha ou confirme conciliação; confirmação real exige human_confirmed=true."""
        return json.dumps(reconcile_bank_transaction(
            agent_id,
            bank_transaction_key=bank_transaction_key,
            account_code=account_code,
            target_type=target_type,
            target_ref=target_ref,
            competence_date=competence_date,
            notes=notes,
            human_confirmed=human_confirmed,
        ), ensure_ascii=False, default=str)

    def finance_reconciliation_report(date_from: str = "", date_to: str = "") -> str:
        """Relate a cobertura da conciliação e exponha pendências."""
        return json.dumps(
            reconciliation_report(agent_id, date_from=date_from, date_to=date_to),
            ensure_ascii=False,
            default=str,
        )

    def finance_accounts_payable(status: str = "", due_from: str = "",
                                 due_to: str = "", limit: int = 500) -> str:
        """Liste contas a pagar registradas no ledger financeiro da empresa."""
        return json.dumps({
            "payables": list_payables(
                agent_id,
                status=status,
                due_from=due_from,
                due_to=due_to,
                limit=limit,
            )
        }, ensure_ascii=False, default=str)

    def finance_record_payable(amount: float, description: str, counterparty: str,
                               due_date: str, competence_date: str = "",
                               account_code: str = "9.9.99", document_ref: str = "",
                               operation_ref: str = "") -> str:
        """Registre obrigação a pagar e sua competência; não executa pagamento."""
        return json.dumps(record_payable(
            agent_id,
            amount=amount,
            description=description,
            counterparty=counterparty,
            due_date=due_date,
            competence_date=competence_date,
            account_code=account_code,
            document_ref=document_ref,
            operation_ref=operation_ref,
        ), ensure_ascii=False, default=str)

    def finance_update_payable(payable_id: str, status: str, paid_at: str = "",
                               bank_transaction_key: str = "") -> str:
        """Atualize estado interno de uma conta a pagar; não movimenta banco."""
        return json.dumps(update_payable(
            agent_id,
            payable_id,
            status=status,
            paid_at=paid_at,
            bank_transaction_key=bank_transaction_key,
        ), ensure_ascii=False, default=str)

    def finance_chart_of_accounts(active_only: bool = True) -> str:
        """Liste o plano de contas gerencial usado por conciliação, fluxo de caixa e DRE."""
        return json.dumps({
            "accounts": list_chart_of_accounts(agent_id, active_only=active_only)
        }, ensure_ascii=False, default=str)

    def finance_upsert_chart_account(code: str, name: str, nature: str,
                                     dre_group: str, cashflow_group: str,
                                     active: bool = True) -> str:
        """Crie ou ajuste uma conta gerencial; não altera o banco."""
        return json.dumps(upsert_chart_account(
            agent_id,
            code=code,
            name=name,
            nature=nature,
            dre_group=dre_group,
            cashflow_group=cashflow_group,
            active=active,
        ), ensure_ascii=False, default=str)

    def finance_cashflow_report(as_of: str = "", forecast_days: int = 30) -> str:
        """Gere fluxo de caixa real + projetado usando Unicred, recebíveis oficiais e contas a pagar."""
        receivables = official_receivables(agent_id, period="aberto")
        return json.dumps(cashflow_report(
            agent_id,
            as_of=as_of,
            forecast_days=forecast_days,
            official_receivables=receivables if receivables.get("ok") else None,
        ), ensure_ascii=False, default=str)

    def finance_dre_report(date_from: str, date_to: str) -> str:
        """Gere DRE gerencial por competência e sinalize tudo que ainda não está classificado."""
        return json.dumps(
            dre_report(agent_id, date_from=date_from, date_to=date_to),
            ensure_ascii=False,
            default=str,
        )

    def finance_position_report() -> str:
        """Gere posição financeira: banco, recebíveis, contas a pagar e conciliação."""
        receivables = official_receivables(agent_id, period="aberto")
        return json.dumps(financial_position_report(
            agent_id,
            official_receivables=receivables if receivables.get("ok") else None,
        ), ensure_ascii=False, default=str)

    tools.extend([
        finance_bank_balance,
        finance_bank_transactions,
        finance_reconciliation_workspace,
        finance_reconcile_transaction,
        finance_reconciliation_report,
        finance_accounts_payable,
        finance_record_payable,
        finance_update_payable,
        finance_chart_of_accounts,
        finance_upsert_chart_account,
        finance_cashflow_report,
        finance_dre_report,
        finance_position_report,
    ])
    return tools


from meuharness.domains.colorglass_finance_reporting import record_accrual

_build_finance_tools_controller = build_finance_tools


def build_finance_tools(agent_id: str) -> list[object]:
    tools = list(_build_finance_tools_controller(agent_id))

    def finance_record_accrual(kind: str, amount: float, description: str,
                               competence_date: str, account_code: str,
                               due_date: str = "", counterparty: str = "",
                               document_ref: str = "", operation_ref: str = "",
                               source: str = "manual", source_ref: str = "") -> str:
        """Registre receita/despesa por competência; não movimenta banco."""
        return json.dumps(record_accrual(
            agent_id,
            kind=kind,
            amount=amount,
            description=description,
            competence_date=competence_date,
            account_code=account_code,
            due_date=due_date,
            counterparty=counterparty,
            document_ref=document_ref,
            operation_ref=operation_ref,
            source=source,
            source_ref=source_ref,
        ), ensure_ascii=False, default=str)

    tools.append(finance_record_accrual)
    return tools


from meuharness.domains.colorglass_finance_reporting import list_dda_bills

_build_finance_tools_with_accrual = build_finance_tools


def build_finance_tools(agent_id: str) -> list[object]:
    tools = list(_build_finance_tools_with_accrual(agent_id))

    def finance_dda_bills(date_from: str = "", date_to: str = "", limit: int = 500) -> str:
        """Liste boletos detectados no DDA Unicred em modo somente leitura."""
        return json.dumps({
            "source": "unicred_dda",
            "read_only": True,
            "bills": list_dda_bills(
                agent_id,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            ),
        }, ensure_ascii=False, default=str)

    tools.append(finance_dda_bills)
    return tools

from meuharness.domains.colorglass_bank_sync import (
    bank_sync_status,
    daily_bank_sync,
    prepare_bank_login,
)

_build_finance_tools_with_dda = build_finance_tools


def build_finance_tools(agent_id: str) -> list[object]:
    tools = list(_build_finance_tools_with_dda(agent_id))

    def finance_bank_sync_status() -> str:
        """Leia o estado seguro da sessão/sincronização bancária sem acessar credenciais."""
        return json.dumps(bank_sync_status(), ensure_ascii=False, default=str)

    def finance_bank_prepare_login() -> str:
        """Prepare a janela bancária isolada. Login continua sendo exclusivamente manual."""
        return json.dumps(prepare_bank_login(), ensure_ascii=False, default=str)

    def finance_bank_daily_sync() -> str:
        """Importe extrato + snapshot DDA completo; sucesso só quando persistência for verificada."""
        return json.dumps(daily_bank_sync(), ensure_ascii=False, default=str)

    tools.extend([
        finance_bank_sync_status,
        finance_bank_prepare_login,
        finance_bank_daily_sync,
    ])
    return tools
