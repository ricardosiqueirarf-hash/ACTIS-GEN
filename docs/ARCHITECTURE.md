# ACTIS GEN — Arquitetura

## Princípio

**Generalizar infraestrutura; especializar conhecimento, estado, contexto, permissões e regras do domínio.**

O ACTIS GEN não deve criar um harness do zero para cada software. Ele cria/configura agentes sobre uma infraestrutura comum e conecta capabilities maduras quando necessário.

## Caminho canônico

```text
Entrada
├─ conversa persistente
├─ chat direto / World
├─ delegação do General
├─ automação
└─ workflow
        ↓
ExecutionService.execute_agent()
        ↓
1. carrega definição do agente
2. seleciona modelo / settings do 9Router
3. resolve tools e scopes efetivos
4. resolve contexto organizacional
5. recupera memória persistente
6. cria Run e publica run.started
7. monta HarnessRequest
8. Harness Core → MAF → 9Router
9. MCP tools quando autorizadas
10. finaliza Run / memória / Event Bus
```

## Camadas

| Camada | Responsabilidade atual |
|---|---|
| `web_assets/index.html` | SPA local: Agentes, World, Automações, Workflows, Approvals, Eventos, Runs, Modelos, Tools, Empresas e Sobre |
| `web.py` | API HTTP local, integração da UI e operações administrativas |
| `execution_service.py` | kernel único de execução |
| `core/` | contratos neutros, Harness, deadline e erros |
| `adapters/maf/` | adaptação para Microsoft Agent Framework e MCPs |
| `providers/nine_router/` | settings, client OpenAI-compatible, catálogo e probe direto |
| `storage.py` | SQLite canônico e migração de JSON legado |
| `agents.py` | agentes, organizações, conversas e Runs |
| `contexts.py` | contexto empresa/setor/projeto/agente |
| `memory.py` | memória cross-conversation com FTS5 |
| `approvals.py` | approvals persistentes e scopes |
| `automations.py` | definição/estado das automações |
| `automation_scheduler.py` | scheduler independente |
| `workflows.py` | editor/persistência/validação/executor de workflows |
| `event_bus.py` | eventos operacionais persistentes |
| 9Router externo | autenticação de providers, catálogo e roteamento/fallback do modelo |

## Fronteiras importantes

1. `core` não depende do domínio, UI, MAF ou 9Router.
2. MAF fica atrás de `adapters/maf`.
3. 9Router resolve provider/modelo; ACTIS GEN resolve agente/capability/contexto/permissão.
4. Browser/Files/Terminal/Computer são capabilities; não devem carregar regra de negócio.
5. Conversation, Run, Automation e Workflow Run são entidades diferentes.
6. Empresa/setor/projeto são espaços de contexto e organização; não são threads de conversa.
7. Memória conversacional e contexto organizacional são sistemas separados.
8. Approvals concedem scopes; não devem ser confundidos com a simples existência de uma tool no catálogo.

## Contexto

```text
empresa
  ↓
setor
  ↓
projeto (binding explícito ao agente)
  ↓
agente
```

Itens `pinned` entram sempre. Os demais são selecionados por sobreposição lexical + prioridade. O pacote de contexto organizacional é entregue separadamente do histórico da conversa.

## Memória

Quando `memory=true`, uma execução concluída grava um resumo textual de usuário + assistente. `memory.py` usa SQLite FTS5 e fallback por recência. O runtime injeta até 6 memórias relevantes. Não há embeddings/vetores nesta versão.

## Permissões

Scopes atuais:

```text
browser.read
browser.interact
files.read
files.write
terminal.exec
computer.view
computer.control
actis.admin
```

Um agente pode ter tools declaradas e uma lista permanente de `permissions`. Approvals aprovados adicionam scopes dinamicamente. Browser, Files e Computer filtram as operações MCP reais por scope.

## Concorrência

- Runs independentes podem existir em paralelo.
- O chat global impede duas requisições simultâneas para **o mesmo agente**.
- Agentes diferentes podem responder simultaneamente.
- Harness Computer é serializado: uma segunda execução com Computer é recusada enquanto outra está ativa.

## Estado do repositório

Auditoria de 2026-09-15: branch `feat/runtime-foundation`, com várias mudanças do ACTIS GEN ainda não commitadas. Portanto esta documentação descreve o **working tree**, não apenas o último commit.
