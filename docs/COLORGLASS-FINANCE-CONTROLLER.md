# ColorGlass Finance Controller

## Objetivo

O agente `financeiro-colorglass` deve atuar como controller financeiro operacional.
Ele recebe ferramentas para produzir relatórios a partir de fatos persistidos, não para
inventar números nem movimentar dinheiro.

## Fontes de verdade

1. **Unicred Bank Bridge (somente leitura)**
   - saldo bancário observado;
   - créditos e débitos normalizados;
   - estado da última sincronização.
2. **ERP ColorGlass / Asaas**
   - contas a receber oficiais;
   - cliente, pedido, parcela, valor, vencimento e status.
3. **Ledger financeiro ACTIS**
   - classificação gerencial;
   - competência;
   - contas a pagar;
   - conciliações confirmadas.
4. **ColorGlass Operations**
   - vínculo operacional de pedido quando existir.

## Tools disponíveis

### Banco / conciliação

- `finance_bank_balance`
- `finance_bank_transactions`
- `finance_reconciliation_workspace`
- `finance_reconcile_transaction`
- `finance_reconciliation_report`

O banco é lido via SQLite em modo `mode=ro`. A conciliação é persistida no ACTIS,
nunca no banco.

`finance_reconcile_transaction(..., human_confirmed=false)` apenas propõe.
A conciliação só vira fato confirmado quando `human_confirmed=true`, após confirmação
explícita na sessão diária de conciliação.

### Contas a pagar

- `finance_accounts_payable`
- `finance_record_payable`
- `finance_update_payable`

Essas tools registram obrigações e baixas internas. Nenhuma delas executa pagamento.

### Recebíveis

- `finance_official_receivables`

Continua sendo a fonte oficial de cobrança/recebíveis do ERP. A tool é read-only.

### Plano de contas e competência

- `finance_chart_of_accounts`
- `finance_upsert_chart_account`
- `finance_record_accrual`

O plano de contas gerencial classifica cada evento para fluxo de caixa e DRE.
`finance_record_accrual` registra receita/despesa na competência correta antes do
pagamento. Depois, a conciliação pode vincular o movimento bancário ao lançamento
existente (`target_type="ledger"`) sem duplicar receita/despesa.

A conta `9.9.99` representa "não classificado" e não entra como resultado da DRE.

### Relatórios

- `finance_cashflow_report`
- `finance_dre_report`
- `finance_position_report`
- `finance_reconciliation_report`
- `finance_daily_report` (legado, mantido por compatibilidade)

## Fluxo de caixa

O realizado usa movimentações bancárias efetivas, independentemente de classificação.
O projetado usa:
- recebíveis oficiais em aberto;
- contas a pagar em aberto.

O relatório sempre retorna cobertura da conciliação e disponibilidade da fonte de
recebíveis.

## DRE gerencial

A DRE usa `competence_date` do ledger, não simplesmente a data do extrato.

Grupos atuais:
- revenue;
- cogs;
- opex;
- financial;
- taxes;
- non_dre.

Investimentos, transferências internas e movimentos de sócios ficam fora da DRE.

A DRE deve retornar `complete=false` quando houver:
- lançamentos do período ainda não conciliados/propostos;
- lançamentos sem classificação DRE;
- ausência de lançamentos classificados no período.

Portanto, zero não significa automaticamente "resultado zero"; pode significar falta
de cobertura, e o relatório deve declarar isso.

## Sessão diária de conciliação

1. Bank Bridge sincroniza o extrato após login manual na Unicred.
2. `finance_reconciliation_workspace` reúne lançamentos não conciliados.
3. O motor gera apenas candidatos determinísticos por valor e, quando possível, nome.
4. O Financeiro apresenta as pendências ao operador.
5. A confirmação humana chama `finance_reconcile_transaction(..., human_confirmed=true)`.
6. A classificação alimenta ledger, contas a pagar, fluxo de caixa e DRE.
7. `finance_reconciliation_report` mostra o que continua pendente.

## Segurança

O Financeiro não recebe tools de:
- PIX;
- TED/transferência;
- pagamento de boleto;
- autorização;
- assinatura;
- criação de favorecido.

Ele pode ler banco e registrar estado gerencial, mas não movimentar recursos.

## Estado validado em 2026-09-18

- Bank Bridge: 19 lançamentos reais visíveis ao Financeiro;
- estado inicial: 19 unreconciled, 0 confirmed;
- runtime do Financeiro: 19 tools financeiras;
- testes focados: 7/7 passando;
- nenhum dos 19 lançamentos reais foi classificado automaticamente durante a implantação.
