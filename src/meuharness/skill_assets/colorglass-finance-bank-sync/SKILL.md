---
name: ColorGlass Finance Bank Sync
description: Sincronização diária, read-only e verificável da Unicred para o Financeiro ColorGlass.
---

# COLORGLASS FINANCE — BANK SYNC

## Objetivo único

Trazer os fatos bancários da Unicred para a base financeira local da ColorGlass:

1. extrato estruturado;
2. todos os boletos DDA disponíveis ao pagador eletrônico;
3. confirmação de persistência local.

Esta skill NÃO concilia contabilmente, NÃO classifica despesas e NÃO paga nada. Ela termina quando o snapshot bancário foi importado e verificado. Conciliação e relatórios vêm depois.

## Interface permitida

Use SOMENTE estas tools para operar o fluxo bancário:

- `finance_bank_sync_status()`
- `finance_bank_prepare_login()`
- `finance_bank_daily_sync()`

Depois de um sync completo, pode usar apenas para leitura:

- `finance_bank_transactions(...)`
- `finance_dda_bills(...)`
- `finance_bank_balance()`
- `finance_reconciliation_workspace(...)`

Nunca use browser genérico, computer/mouse/teclado, terminal, CDP, requests HTTP ou URLs bancárias diretamente. A implementação do Bank Bridge é a única camada autorizada a conversar com a sessão bancária.

## Proibições absolutas

- Nunca solicitar, ler, armazenar, repetir ou registrar senha, token, OTP, UniToken, assinatura eletrônica, cookie, refresh token ou access token.
- Nunca preencher login pelo agente. O login é manual do humano.
- Nunca chamar rota de PIX, transferência, pagamento, agendamento, boleto, assinatura, favorecido ou autorização.
- Nunca ocultar/exibir título DDA, alterar DDA ou executar qualquer mutação no banco.
- Nunca considerar uma captura parcial como sucesso.
- Nunca substituir DDA ausente por inferência do extrato.
- Nunca apagar o snapshot DDA anterior quando a captura nova falhar.
- Nunca fazer loop agressivo de login ou retry.

## Máquina de estados obrigatória

### S0 — INSPECT

Primeira ação: `finance_bank_sync_status()`.

Antes de abrir/sincronizar o banco, examine `last_extrato_sync` e `last_dda_sync`.
Se ambos estiverem com `status=ok` e `finished_at` for de hoje, trate o snapshot bancário
do dia como fresco e vá direto para S5. Só sincronize novamente no mesmo dia se o humano
pedir explicitamente atualização, retry ou nova leitura.

Estados aceitos:

- `authentication=authenticated` → vá para S2.
- `authentication=auth_required` → vá para S1.
- `authentication=browser_unavailable` → chame `finance_bank_prepare_login()` UMA vez e vá para S1.
- qualquer outro estado → FAIL_CLOSED.

### S1 — WAIT_HUMAN_AUTH

Informe objetivamente que a janela isolada da Unicred precisa de login manual.

Pare a Run. Não fique consultando a sessão em loop e não tente autenticar.

Quando o humano disser que entrou, uma nova execução começa novamente em S0.

### S2 — SYNC

Chame exatamente uma vez:

`finance_bank_daily_sync()`

Essa tool é o único comando que pode disparar importação bancária.

Ela deve importar:

- extrato;
- DDA visível;
- DDA oculto;
- todas as páginas;
- todas as janelas necessárias impostas pelo banco;
- deduplicação;
- persistência local.

### S3 — VERIFY

Só considere a importação concluída se o retorno tiver simultaneamente:

- `ok=true`
- `complete=true`
- `partial=false`
- `extrato.persisted_status="ok"`
- `dda.persisted_status="ok"`
- `dda.error=null`

`dda.fetched=0` pode ser válido. Zero títulos não é erro por si só; o critério é a varredura ter terminado completa e verificada.

Se qualquer condição acima falhar, o estado é PARTIAL/FAILED.

### S4 — READBACK

Após S3, chame `finance_bank_sync_status()` novamente.

Confirme que os dois últimos syncs persistidos estão `ok`.

Somente depois disso o snapshot bancário do dia pode ser tratado como fresco.

### S5 — HANDOFF

Com o snapshot verificado:

- extrato alimenta conciliação bancária e caixa realizado;
- DDA alimenta obrigações detectadas / A Pagar e fluxo de caixa;
- DDA não classificado NÃO entra automaticamente na DRE;
- classificação contábil ocorre em outra etapa, com regras próprias.

A skill termina aqui.

## Garantia "TODOS OS BOLETOS"

A palavra "todos" significa todos os títulos retornáveis pelo DDA da conta, não apenas a tela inicial.

Uma execução só pode marcar `complete=true` se o Bank Bridge tiver concluído o contrato de coleta integral:

1. títulos visíveis;
2. títulos ocultos;
3. todas as páginas de cada consulta;
4. todas as janelas de datas exigidas pelo backend;
5. deduplicação por identificador estável;
6. atualização atômica do snapshot ativo.

Se uma única página/janela falhar, DDA é parcial e o snapshot anterior permanece sendo a última base completa conhecida.

## Idempotência

Reexecutar o sync deve ser seguro:

- extrato usa chave estável e não duplica lançamentos;
- DDA usa chave estável e não duplica boletos;
- um snapshot DDA completo atualiza o conjunto ativo;
- títulos não vistos em um snapshot completo podem ficar inativos, preservando histórico;
- captura parcial não substitui snapshot completo anterior.

## Tratamento de falhas

### AUTH_REQUIRED
Pare e peça login manual. Não faça retry automático.

### DDA_CAPTURE_TIMEOUT / DDA parcial
Extrato pode ter sido atualizado, mas o ciclo bancário do dia permanece INCOMPLETO.
Não diga "sincronização concluída".
Não use o novo DDA para afirmar que todos os boletos foram carregados.

### EXTRATO falhou
Ciclo INCOMPLETO. Não derive caixa realizado do sync atual.

### TIMEOUT geral
Ciclo INCOMPLETO. Preserve último snapshot válido e reporte o bloqueio.

### saída inválida / bridge error
FAIL_CLOSED. Não tente improvisar por browser/terminal.

## Observabilidade e resposta

Durante rotina normal, reporte somente:

- estado: completo / auth_required / parcial / falhou;
- horário do último sync;
- quantidade de lançamentos do extrato;
- quantidade de boletos DDA ativos;
- novos/existentes quando disponível.

Não despeje valores, descrições, beneficiários ou dados bancários sensíveis sem pedido explícito.

## Critério final

Nunca diga "banco sincronizado" apenas porque a tool terminou.

A frase de sucesso só é permitida após S3 + S4:

`complete=true` e readback persistido de extrato + DDA com status `ok`.
