# ACTIS GEN — Current State

Atualizado: 2026-09-16

Este arquivo é o ponto de entrada curto para agentes. Para detalhes completos, consulte `ACTIS-GEN-ESTADO-ATUAL.md`.

## Estado atual
- Agents: ativo
- Conversations: ativo e separado de Runs
- Runs: ativo, com lifecycle, heartbeat e cancelamento
- World: ativo
- Automations: ativo
- Workflows: executáveis
- Approvals: ativo
- Event Bus: ativo
- Memory: SQLite + FTS5
- Context: empresa → setor → projeto → agente
- Browser / Files / Terminal / Computer: capabilities via harness/MCP
- Connectors / ACTIS Config: ativo
- Agent Bus / Canais: ativo
- Feature registry / General: ativo; catálogo descobrível + administração/delegação
- WhatsApp local-first: ativo no working tree; shadow, inbox/outbox, sync e automação por agente
- Desktop assistant: experimental no working tree

## Arquitetura canônica
Entradas convergem em `ExecutionService.execute_agent()`.
ACTIS resolve agente, contexto, memória, tools e permissões.
MAF executa; 9Router resolve provider/modelo; harnesses/MCPs realizam ações externas.

## Foco atual
Consolidar o ACTIS como infraestrutura reutilizável para agentes que operam por harnesses especializados, evitando criar runtimes paralelos por domínio.

## General
A skill `actis.control-plane` usa `feature_registry.py` + estado vivo para descobrir o sistema atual. O General administra diretamente agentes, organização/contexto, Runs, approvals, automações, workflows, connectors e canais; também consegue inspecionar/configurar/sincronizar agentes WhatsApp. Ações de domínio que pertencem a outro agente são delegadas pelo kernel, em vez de copiar silenciosamente a identidade/capabilities daquele agente.

Regra de desenvolvimento: toda feature relevante nova precisa definir seu comportamento no General e ter teste de regressão dessa integração.

## PDF input/output
- Tool nativa `pdf_toolkit`: inspect/create/edit/merge/render com scopes `pdf.read` e `pdf.write`.
- Skill herdável recomendada: `pdf.standard-pack` → `pdf.create` + `pdf.edit` → `pdf.operator`.
- Chat aceita imagem e PDF como input (até 4 anexos; PDF até 15 MB). PDFs são persistidos em `~/.local/share/actis-gen/attachments/`, validados e o texto é extraído com referência de página para contexto do agente.
- O arquivo original permanece disponível por `local_path`, permitindo que agentes com `pdf.standard-pack` leiam e editem o mesmo documento sem reenviar o PDF ao modelo.
- O General descobre essa capability pelo catálogo vivo de Tools/Skills e pode atribuir `pdf.standard-pack` a qualquer agente autorizado.

## UI
Fonte canônica: `VISUAL-SYSTEM.md`.
Identidade: pixelada + gamificada + técnica + dark/austera.

## Runtime local observado
- ACTIS web: `127.0.0.1:8765`
- 9Router: `127.0.0.1:20128`
- Browser CDP: porta local dinâmica por agente
- Scheduler: serviço systemd user
- Persistência: SQLite em `~/.local/share/actis-gen/actis.db`

## Working tree
Branch observado: `feat/runtime-foundation`.
Existem várias alterações locais e arquivos ainda não commitados. Preserve-os.

## Onde aprofundar
- Estado completo: `ACTIS-GEN-ESTADO-ATUAL.md`
- Arquitetura: `ARCHITECTURE.md`
- Visual: `VISUAL-SYSTEM.md`
- Execução: `EXECUTION.md`
- API: `API.md`
- Operação: `OPERATIONS.md`

## Regra de precedência
`código/runtime local > CURRENT-STATE.md > ACTIS-GEN-ESTADO-ATUAL.md > demais docs > README`
