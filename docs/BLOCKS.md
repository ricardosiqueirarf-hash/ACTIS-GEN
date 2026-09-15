# ACTIS GEN — Blocos de implementação

## Concluído no working tree atual

### Fundação de execução

- contratos neutros do Harness;
- MAF adapter;
- gateway 9Router;
- deadlines, erros normalizados e smoke test.

### Control plane

- registro persistente de agentes;
- UI/API local;
- conversas persistentes;
- Runs e cancelamento;
- World e chat global;
- empresas/setores com drag and drop.

### Capabilities

- catálogo de Tools;
- Browser Harness;
- Filesystem MCP;
- Terminal MCP;
- Computer Use MCP;
- ACTIS Admin.

### Contexto e memória

- contexto hierárquico empresa/setor/projeto/agente;
- projects + bindings;
- prioridade e pinned context;
- memória cross-conversation com FTS5.

### Segurança operacional

- scopes granulares;
- permissions permanentes por agente;
- approvals persistentes aprovar/negar/revogar;
- filtragem real das operações MCP por scope.

### Autonomia

- automações interval/daily/once/condition;
- scheduler independente em processo separado;
- Event Bus persistente;
- heartbeats para Computer Use.

### Workflows

- editor visual;
- Start / Agent / Condition / End;
- validação estrutural;
- execução via API/UI;
- branches true/false;
- trace e histórico em `workflow_runs`;
- limite anti-loop de 50 etapas.

### Persistência

- SQLite canônico em WAL;
- migração automática de JSON legado;
- collections + events + memories + approvals + workflows + workflow_runs.

## Próximos blocos recomendados

1. **Consolidar qualidade do working tree**: zerar os 4 findings atuais de Ruff e commitá-lo em checkpoints claros.
2. **Workflow v2**: paralelismo, fan-in/fan-out, retries explícitos, approvals no meio do grafo e nós de tool/evento.
3. **Autenticação/RBAC** se o produto deixar de ser exclusivamente local single-user.
4. **Memória v2** somente se FTS5 demonstrar limite real: embeddings, deduplicação e compactação.
5. **Observabilidade v2**: filtros, correlação Run ↔ workflow ↔ automation ↔ tool calls e métricas agregadas.
6. **Storage/schema v2**: migrar entidades críticas de `collections(payload JSON)` para tabelas tipadas quando consultas/concorrência exigirem.
7. **Packaging/deploy**: instalação reproduzível do web + scheduler + Browser/Computer dependencies.

Não criar novas abstrações só por antecipação. Cada bloco deve responder a um uso real do ACTIS GEN ou de um domínio consumidor.
