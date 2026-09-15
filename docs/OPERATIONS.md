# ACTIS GEN — Operação local

## Processos observados em 2026-09-15

```text
actis-gen-web       → 127.0.0.1:8765
9Router             → 127.0.0.1:20128
Browser CDP         → porta local dinâmica por agente (9222–9822)
actis-gen-scheduler → systemd --user, ativo
```

## Scheduler

Executável:

```bash
actis-gen-scheduler --poll-seconds 5
```

Serviço instalado nesta máquina:

```text
~/.config/systemd/user/actis-gen-scheduler.service
```

Comandos úteis:

```bash
systemctl --user status actis-gen-scheduler.service
systemctl --user restart actis-gen-scheduler.service
journalctl --user -u actis-gen-scheduler.service -f
```

O scheduler executa automações diretamente pelo `execute_agent()`. Ele não depende do processo web estar rodando.

## Storage

```text
~/.local/share/actis-gen/actis.db
```

SQLite usa WAL e foreign keys. Principais tabelas:

- `collections` — entidades JSON ordenadas;
- `events` — Event Bus;
- `memories` e `memories_fts`;
- `approvals`;
- `workflows`;
- `workflow_runs`.

Na auditoria de 2026-09-15 havia:

```text
7 agentes
62 runs
7 conversas
2 empresas
2 setores
0 automações
14 memórias
2 approvals
2 workflows
0 workflow_runs
1 projeto
0 context_items
0 agent_context_bindings
30 eventos
```

Esses números são apenas snapshot e mudam com o uso.

## Migração de JSON

`agents.py`, `contexts.py` e `automations.py` chamam `import_legacy_json()` antes da leitura. O JSON só é importado se a collection SQLite correspondente ainda estiver vazia. Depois disso, o SQLite é a fonte canônica.

## Verificação

Use o escopo correto do ACTIS:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/meuharness tests
```

Não use `ruff check .` como indicador isolado porque `upstream/agent-framework` possui código/configuração próprios.

Auditoria atual:

```text
pytest: 48 passed
ruff: 4 findings no código ACTIS
```

## Git

Na auditoria, branch `feat/runtime-foundation`. Há várias alterações/untracked files do ACTIS GEN ainda fora de commit. Antes de releases, criar checkpoints e garantir que documentação e código versionado representem o mesmo estado.

## Segurança operacional

A API é local single-user e não tem autenticação. Browser, Terminal, Files e Computer podem produzir side-effects reais. Não exponha `8765`, `9222` ou capabilities locais para rede não confiável.

## Browser Harness gerenciado

O ACTIS inicia e reaproveita um Chrome headless exclusivo por agente. O perfil persistente fica em `~/.local/share/actis-gen/browser-profiles/<agent-id>/`; cookies, logins e abas pertencem àquele agente. A porta CDP é escolhida localmente entre 9222–9822 e registrada em `runtime.json`. Se o processo morrer, a próxima execução relança o Chrome com o mesmo perfil. O browser não usa mouse, teclado, foco ou workspace do desktop humano.
