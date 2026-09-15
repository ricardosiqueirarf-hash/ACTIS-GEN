# ACTIS GEN — Execução e lifecycle

## Unidade básica: Run

Uma Run representa uma execução de um agente. Conversation é histórico de diálogo; Run é execução; Automation agenda Runs; Workflow Run orquestra várias Runs.

Estados de Run observados:

```text
running
completed
failed
cancelled
```

## Fluxo de `execute_agent()`

1. valida `agent_id` e prompt;
2. normaliza até 16 mensagens anteriores + mensagem atual;
3. carrega o modelo configurado no agente;
4. calcula tools efetivas;
5. calcula scopes permanentes + approvals aprovados;
6. impede concorrência de Computer Use;
7. amplia deadline para no mínimo 300 s quando Computer está ativo;
8. monta MCPs filtrados pelos scopes;
9. resolve contexto organizacional;
10. recupera memória FTS5 quando `memory=true`;
11. cria Run;
12. emite `run.started`;
13. executa `HarnessRequest` via Harness → MAF → 9Router;
14. trata cancelamento/falha;
15. persiste resposta e memória;
16. detecta pedidos `ACTIS_APPROVAL_REQUEST: <scope>` na resposta;
17. emite `run.completed`.

## Heartbeat de Computer Use

Runs com Harness Computer publicam `run.heartbeat` a cada 10 s enquanto ativas. O heartbeat é observabilidade; não é garantia de progresso da aplicação externa.

## Cancelamento

`POST /api/runs/:id/cancel` tenta cancelar a task asyncio ativa. Se não houver task registrada, o backend pode marcar uma Run órfã como `cancelled`. Cancelamento local não prova rollback de side-effects externos já executados.

## Tools efetivas

Uma tool entra no runtime quando:

- está declarada no agente; ou
- algum scope aprovado corresponde a essa tool.

A lista de operações internas do MCP depende dos scopes efetivos. Exemplo: `harness_files` pode existir, mas apenas os métodos de leitura serão expostos se o agente tiver somente `files.read`.

## Approvals

O modelo é instruído a pedir capacidade ausente com uma linha exata:

```text
ACTIS_APPROVAL_REQUEST: files.write
```

Depois da conclusão da Run, o kernel cria um approval persistente para scopes válidos encontrados. A autorização passa a valer em execuções posteriores após aprovação na UI/API.

## Conversas

Conversation persiste mensagens. Cada envio cria uma Run independente vinculada por `conversation_id`. O contexto da execução usa até 16 mensagens válidas anteriores.

## Delegação

`actis_run_agent` usa o mesmo `execute_agent()` do chat. Portanto o agente alvo recebe seu modelo, tools, permissions, contexto e memória normais; não existe um caminho “simplificado” separado para delegação.

## Automação

O scheduler executa diretamente `execute_agent(source="automation")`. Conditions são verificadas pelo General com `source="automation_condition"`, esperando `ACTIS_TRIGGER` ou `ACTIS_WAIT`.

## Workflows

O executor de workflow suporta:

- `start` — ponto inicial único;
- `agent` — executa um agente via `execute_agent()`;
- `condition` — roteia branch `true`/`false` usando a saída anterior;
- `end` — encerra e devolve a saída.

Templates de prompt aceitam:

```text
{{input}}
{{previous}}
{{node.label}}
```

Condições atuais são simples e determinísticas: `contains:`, `not contains:`, `equals:` ou substring. Há limite de 50 etapas para detectar ciclos sem saída.

Cada execução gera registro em `workflow_runs` com trace por nó. Em 2026-09-15 o engine e a UI estão implementados, mas o banco auditado tinha 0 workflow runs persistidas.

## Erros do Harness Core

O core ainda normaliza categorias como configuração, autenticação, rate limit, timeout, conexão, gateway e resposta inválida. 9Router continua dono do fallback upstream; o ACTIS GEN não repete automaticamente uma ação com side-effect.
