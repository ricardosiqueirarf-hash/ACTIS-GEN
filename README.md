# ACTIS GEN

ACTIS GEN é um **control plane local para agentes de IA**. A ideia central é generalizar a infraestrutura e especializar cada agente por modelo, instruções, contexto, memória, ferramentas, permissões e posição organizacional.

O projeto atual já inclui UI local, agentes persistentes, conversas, Runs, World, automações, workflows executáveis, approvals granulares, Event Bus, memória persistente, contexto hierárquico e integração com Browser/Files/Terminal/Computer via MCP.

## Arquitetura resumida

```text
UI / API / World / Conversation / Automation / Workflow / Delegation
                              ↓
                         execute_agent()
                              ↓
            contexto + memória + scopes + tools + timeout
                              ↓
                    Harness Core → MAF
                              ↓
                         9Router
                              ↓
                     provider / modelo
                              ↘
                        MCP Tools
```

O `ExecutionService` é o kernel canônico. O 9Router continua responsável por provider/modelo; o ACTIS GEN controla agente, contexto, permissões, lifecycle e operação.

## Componentes implementados

- agentes persistentes com modelo, instruções, empresa/setor, tools, scopes e memória;
- conversas persistentes separadas de Runs;
- World com visão operacional e chat global assíncrono entre agentes diferentes;
- automações `interval`, `daily`, `once` e `condition`;
- scheduler independente `actis-gen-scheduler`;
- workflows visuais executáveis com Start / Agent / Condition / End;
- approvals persistentes por scope;
- Event Bus persistente em SQLite;
- memória cross-conversation com SQLite FTS5;
- contexto hierárquico empresa → setor → projeto → agente;
- Browser, Files, Terminal e Computer Use via MCP;
- modelo/provider via 9Router OpenAI-compatible;
- probe operacional de modelo sem depender da sessão administrativa do dashboard.

## Persistência

A fonte canônica é:

```text
~/.local/share/actis-gen/actis.db
```

SQLite roda em WAL. JSON/JSONL antigos são apenas fontes de migração quando uma coleção ainda está vazia.

## Instalação local

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Configure `NINEROUTER_API_KEY` e, para execução normal, um `NINEROUTER_MODEL` válido.

## Comandos

```bash
actis-gen chat
actis-gen-web
actis-gen-scheduler
actis-gen-smoke
```

A UI padrão roda em `http://127.0.0.1:8765`. O 9Router local observado nesta máquina roda em `127.0.0.1:20128`; o Browser Harness inicia/reutiliza um Chrome headless isolado por agente, com perfil persistente e porta CDP local dinâmica.

## Validação auditada em 2026-09-15

```text
pytest: 48/48 passed
ruff src/meuharness tests: 4 findings atuais
scheduler independente: active
SQLite: active
web: 127.0.0.1:8765
9Router: 127.0.0.1:20128
Browser CDP: gerenciado por agente, portas locais dinâmicas 9222–9822
```

Os 4 findings de Ruff atuais estão em `web.py`/`workflows.py` e não impediram os 46 testes de passar. Não trate `ruff check .` como métrica do ACTIS porque o repositório contém `upstream/agent-framework`, que possui seu próprio lint/configuração.

## Documentação

- `docs/ACTIS-GEN-ESTADO-ATUAL.md` — fotografia completa do produto atual;
- `docs/ARCHITECTURE.md` — arquitetura e fronteiras;
- `docs/EXECUTION.md` — lifecycle de uma execução;
- `docs/API.md` — API HTTP local;
- `docs/OPERATIONS.md` — processos, storage e operação local;
- `docs/MAF-INTEGRATION.md` — MAF e MCP;
- `docs/MODEL-GATEWAY.md` — 9Router e modelos;
- `docs/BLOCKS.md` — roadmap por blocos;
- `docs/DIAGNOSTIC-2026-09-14.md` — registro histórico do diagnóstico inicial.
