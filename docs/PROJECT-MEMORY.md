# ACTIS GEN — Project Memory MVP

## Objetivo

Adicionar memória longa por projeto sem substituir a memória cross-conversation do agente.

- memória do agente: SQLite + FTS5;
- memória do projeto: wiki Markdown em `.actis-memory/`;
- vínculo: `project_id` do ACTIS → workspace local;
- recuperação: metadata primeiro, corpo apenas quando relevante.

## Layout

```text
<workspace>/.actis-memory/
├── INDEX.md
├── observation/
├── decision/
├── gotcha/
└── workstream/
```

Cada página possui frontmatter com ID, tipo, título, resumo, tags, status e timestamps.
`INDEX.md` contém somente metadata e pode ser versionado junto do projeto.

## Semântica

- `observation`: observação ainda não verificada; nasce como `unverified`.
- `decision`: decisão deliberada/confirmada do projeto.
- `gotcha`: armadilha, restrição ou comportamento confirmado.
- `workstream`: frente de trabalho persistente.

Uma observation não muda de categoria silenciosamente. Para promovê-la é obrigatório
informar uma `verification_note`; a página de origem é preservada e passa a apontar
para a memória promovida.

## Progressive disclosure

1. O ACTIS varre somente o frontmatter das páginas.
2. Título, resumo e tags são comparados lexicalmente com o prompt atual.
3. Somente as páginas selecionadas têm o corpo carregado.
4. O contexto injetado identifica projeto, tipo e status.
5. Código/runtime e instrução atual continuam com precedência.

Agentes sem projeto ligado ou projetos sem binding de workspace não recebem essa camada.

## General

A feature aparece como `project-memory` no catálogo canônico. O General dispõe de:

- `actis_bind_project_memory`
- `actis_project_memory_catalog`
- `actis_project_memory_recall`
- `actis_project_memory_write`
- `actis_project_memory_promote`

Fluxo mínimo:

1. criar/identificar um projeto ACTIS;
2. ligar o agente ao projeto;
3. vincular o projeto ao diretório local;
4. registrar decisions/gotchas/workstreams ou observations;
5. nas Runs seguintes, páginas relevantes entram automaticamente.

## Fora do MVP

Captura/consolidação automática, embeddings, entity graph, resolução automática de
contradições e políticas de retenção ficam para versões seguintes. O contrato básico
permanece: páginas Markdown versionáveis, metadata leve e promoção explícita de
informação não verificada.
