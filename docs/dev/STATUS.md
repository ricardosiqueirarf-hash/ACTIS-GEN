# ACTIS GEN — Development Status

Atualizado: 2026-09-18T14:36:00-03:00

A `main` consolidada é a fonte canônica do código. `docs/dev/active/` contém somente trabalhos ainda abertos.

## Tarefas ativas

| ID | Tarefa | Estado | Agente | Próximo passo |
|---|---|---|---|---|
| DEV-2026-09-17-002 | WhatsApp PDF document delivery hardening | IN_PROGRESS | ChatGPT-whatsapp-pdf-finish | Executar a tentativa final autorizada e reconciliar ACK sem retry ambíguo. |
| DEV-2026-09-17-003 | ACTIS GEN Android current-state APK | IN_PROGRESS | ChatGPT-actis-gen-android-build | Atualizar o worktree Android e validar o build atual. |
| DEV-2026-09-17-005 | Espinha dorsal administrativa ColorGlass | IN_PROGRESS | ChatGPT-colorglass-order-backbone | Reconciliar o handoff com os módulos operacionais já implementados e concluir o escopo restante. |
| DEV-2026-09-18-004 | Fase 1 — Fundação Empresarial ACTIS | IN_PROGRESS | opencode | Concluir isolamento tenant e testes cross-tenant sem quebrar APIs legadas. |

## Consolidadas em 2026-09-18

Humanizer (001), memória de projeto (002), Company Workspace (003), relatório financeiro via WhatsApp (005), gerente de Runs (006), gateway shadow WhatsApp (007), Baileys primário/fallback (008), assistente financeiro (009) e Bank Sync Unicred/DDA (010) possuem handoffs em `docs/dev/completed/`.

## Regras de continuidade

1. Não recriar feature registrada em `completed/`.
2. Antes de alterar uma tarefa ativa, reconciliar handoff, `git status` e runtime atual.
3. Mudanças concluídas devem terminar com testes e commit na base canônica; não deixar versões staged/unstaged divergentes.
4. IDs DEV são únicos entre `active/` e `completed/`.
