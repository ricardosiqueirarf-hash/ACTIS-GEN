# ACTIS GEN — Estado Atual

> Auditoria do **working tree real** em 2026-09-15. O branch observado é `feat/runtime-foundation` e existem várias alterações ainda não commitadas. Este documento descreve o código/serviços em execução, não apenas o último commit.

## 1. O que é

ACTIS GEN é um **control plane local para agentes de IA**. O objetivo é reutilizar infraestrutura de execução e harnesses em vez de criar um harness específico do zero para cada software ou domínio.

```text
generalizar infraestrutura
especializar agente + conhecimento + estado + contexto + permissões + tools
```

Um agente combina identidade, modelo, instruções, organização, contexto, memória, tools e permissions. O ACTIS GEN coordena; MAF executa; 9Router resolve provider/modelo; MCPs realizam ações externas.

## 2. Arquitetura real

```text
Conversation / World / Direct Chat / Automation / Workflow / Delegation
                               ↓
                         execute_agent()
                               ↓
             agente + modelo + contexto + memória
             tools + scopes + approvals + timeout
                               ↓
                         Harness Core
                               ↓
                 Microsoft Agent Framework
                               ↓
                           9Router
                               ↓
                      provider / modelo
                               ↘
 Browser Harness / Filesystem / Terminal / Computer Use
```

`execution_service.py` é o kernel canônico. Não há engines separadas para chat, automação ou delegação.

## 3. Agentes e conversas

Agentes persistem, entre outros campos:

- `id`, `name`, aparência;
- `company_id`, `sector_id`;
- `model`, `instructions`;
- `tools`, `permissions`;
- `memory`, `status`, timestamps.

Conversa e Run são entidades diferentes. Conversas mantêm mensagens persistentes e cada envio gera uma Run. O runtime usa até 16 mensagens anteriores válidas como contexto conversacional.

O agente `general` recebe ACTIS Admin e consegue administrar agentes, modelos, Runs, automações e delegar tarefas via tools `actis_*`.

## 4. Chat global / World

World organiza visualmente agentes por empresa/setor e mostra atividade recente/delegações. O chat global permite chamar agentes diretamente.

Concorrência real:

- agentes diferentes podem processar solicitações simultaneamente;
- o mesmo agente não recebe duas requisições diretas/globais simultâneas;
- a API devolve `409` quando aquele agente já está ocupado nesse caminho.

## 5. Contexto hierárquico

Contexto organizacional possui quatro scopes:

```text
empresa
  ↓
setor
  ↓
projeto
  ↓
agente
```

Projetos são vinculados explicitamente a agentes por `agent_context_bindings`.

Itens de contexto possuem `kind`, título, conteúdo, prioridade, `pinned`, source e estado ativo. Itens pinned são sempre incluídos; os demais são recuperados por relevância lexical + prioridade.

Kinds atuais: instruction, knowledge, document, fact, note, procedure, state e reference.

## 6. Memória persistente

Quando `memory=true`, execuções concluídas gravam memória textual cross-conversation em SQLite.

Storage:

```text
memories
memories_fts
```

Retrieval atual usa FTS5 e fallback por recência. Até 6 memórias são injetadas pelo kernel por Run. A mensagem atual e o contexto explícito têm precedência sobre memória antiga.

Não há embeddings/banco vetorial nesta versão.

## 7. Tools, permissions e scopes

Tools atuais:

| Tool | Scopes |
|---|---|
| Harness Browser | `browser.read`, `browser.interact` |
| Harness Files | `files.read`, `files.write` |
| Harness Terminal | `terminal.exec` |
| Harness Computer | `computer.view`, `computer.control` |
| ACTIS Admin | `actis.admin` |

A configuração permanente do agente pode selecionar tools e, separadamente, scopes em `permissions`.

Browser, Files e Computer filtram **as operações MCP reais**. Ex.: `files.read` não expõe write/edit/move; `computer.view` não expõe click/type.

## 8. Approvals

Scopes extras podem ser pedidos pelo agente com:

```text
ACTIS_APPROVAL_REQUEST: files.write
```

Ao finalizar a Run, o kernel cria um approval persistente para scopes válidos detectados. A aba Approvals permite:

- aprovar;
- negar;
- revogar approval anteriormente aprovado.

Approvals aprovados entram nos scopes efetivos das Runs seguintes.

## 9. Runs, cancelamento e Event Bus

Run states atuais incluem:

```text
running
completed
failed
cancelled
```

Lifecycle típico:

```text
run.started
   ↓
[run.heartbeat]*
   ↓
run.completed | run.failed | run.cancelled
```

Runs ativas podem ser canceladas pela UI/API. O Event Bus está em SQLite e a aba Eventos consulta os eventos a cada 2 segundos.

## 10. Computer Use

Computer Use controla o desktop Linux/Wayland real via Zavora Computer Use MCP.

- `computer.view`: observação/metadata;
- `computer.control`: mouse, teclado, scroll, apps/janelas etc.

`computer_observe` captura screenshot e usa um modelo multimodal via 9Router para produzir descrição + coordenadas físicas.

Runs com Computer recebem deadline mínimo de 300 s e heartbeat a cada 10 s.

Somente uma Run de Computer pode ficar ativa por vez no processo ACTIS GEN.

## 11. Automações

Triggers implementados:

- `interval` — recorrência em segundos/minutos;
- `daily` — horário diário;
- `once` — execução única;
- `condition` — condição avaliada periodicamente pelo General.

O scheduler chama `execute_agent()` diretamente. Conditions pedem ao General resposta `ACTIS_TRIGGER` ou `ACTIS_WAIT` e disparam apenas na transição para condição ativa.

O scheduler é independente do web e, nesta máquina, roda como serviço systemd user:

```text
actis-gen-scheduler.service
```

## 12. Workflows

Workflows **já são executáveis**, não apenas visuais.

Tipos de nó:

- Start;
- Agent;
- Condition;
- End.

O workflow exige exatamente 1 Start e ao menos 1 End. Agent nodes chamam `execute_agent()`; Condition nodes escolhem edges `true` ou `false`.

Condições atuais:

```text
contains:termo
not contains:termo
equals:texto
substring simples
```

Templates de prompt:

```text
{{input}}
{{previous}}
{{node.label}}
```

Há limite de 50 etapas para evitar ciclo infinito. Workflow Runs persistem status, input, output, error e trace por nó em `workflow_runs`.

A UI possui editor, inspector, validação, botão Executar e lista de Runs recentes.

Na auditoria atual existiam 2 workflows salvos e 0 workflow runs persistidas.

## 13. Persistência SQLite

Fonte canônica:

```text
~/.local/share/actis-gen/actis.db
```

SQLite usa WAL e foreign keys.

Tabelas estruturadas:

- `events`;
- `memories`;
- `memories_fts`;
- `approvals`;
- `workflows`;
- `workflow_runs`.

Entidades mais flexíveis ficam em `collections(name, item_id, payload, position)`, incluindo agents, runs, companies, sectors, conversations, automations, context_items, projects e bindings.

JSON/JSONL anteriores são somente legado de migração: são importados quando a collection SQLite correspondente está vazia.

## 14. Modelos e 9Router

9Router é gateway OpenAI-compatible e, na máquina auditada, está em:

```text
127.0.0.1:20128
```

O ACTIS GEN possui dois diagnósticos:

- `POST /api/model-probe`: completion real e independente da sessão administrativa do dashboard;
- teste de providers/connections: diagnóstico administrativo, podendo depender da sessão do 9Router no browser.

O ACTIS não implementa fallback próprio; fallback/routing upstream pertencem ao 9Router.

## 15. Interface atual

- **Agentes** — cadastro, chat, conversas e configuração;
- **World** — visão operacional, organização e chat global;
- **Automações** — builder + estado das rotinas;
- **Workflows** — editor, validação, execução e histórico;
- **Approvals** — permissões pendentes/aprovadas/negadas/revogadas;
- **Eventos** — telemetria live;
- **Runs** — ativas, histórico e cancelamento;
- **Modelos** — catálogo, saúde e providers;
- **Tools** — capabilities;
- **Empresas** — empresas, setores, projetos, drag/drop e contexto;
- **Sobre** — documentação interna.

## 16. API

A API HTTP local cobre agentes, companies, sectors, projects, context, bindings, conversations, automations, approvals, workflows/workflow-runs, memory, Runs, Events, storage, models e providers.

Detalhes completos: `docs/API.md`.

## 17. Processos observados

Na auditoria de 2026-09-15:

```text
ACTIS web        127.0.0.1:8765
9Router          127.0.0.1:20128
Browser CDP      porta local dinâmica por agente (9222–9822)
Scheduler        systemd --user active
```

## 18. Snapshot do banco

```text
agents                  7
runs                   62
conversations            7
companies                2
sectors                  2
automations              0
memories                14
approvals                2
workflows                2
workflow_runs            0
projects                 1
context_items            0
agent_context_bindings   0
events                  30
```

Esses números são snapshot, não contrato.

## 19. Validação real

Auditoria atual:

```text
pytest -q
→ 48 passed

ruff check src/meuharness tests
→ 4 findings
```

Os 4 findings atuais são:

- import formatting em `web.py`;
- import `asyncio` não usado em `workflows.py`;
- expressão redundante em `_normalize_node()`;
- `except Exception` amplo no executor de workflow.

Logo, a documentação anterior que dizia **“Ruff OK” estava desatualizada**.

`ruff check .` não é a métrica correta porque também varre `upstream/agent-framework`, que possui lint/configuração próprios.

## 20. Estado Git

Branch auditado:

```text
feat/runtime-foundation
```

Há várias modificações e arquivos untracked do ACTIS GEN. Antes de tratar este estado como release, é recomendável consolidar commits/checkpoints.

## 21. Limites reais restantes

- local single-user, sem autenticação/RBAC/tenancy;
- memória sem embeddings/vetores;
- conditions de workflow ainda são simples, não um expression engine completo;
- workflow executor é sequencial: sem fan-out/fan-in/paralelismo nativo;
- scheduler é independente, mas a instalação systemd atual é específica desta máquina;
- entidades em `collections` ainda são JSON payloads, não schemas SQL totalmente tipados;
- API local não deve ser exposta publicamente no estado atual;
- working tree ainda precisa de consolidação Git e dos 4 findings de lint.
