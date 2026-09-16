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
3. resolve tools, skill sets e scopes efetivos
4. herda skill sets explicitamente herdáveis quando existe parent_agent_id
5. resolve contexto organizacional
6. recupera memória persistente
7. cria Run e publica run.started
8. monta HarnessRequest
9. Harness Core → MAF → 9Router
10. MCP/native tools quando autorizadas
11. finaliza Run / memória / Event Bus
```

## Camadas

| Camada | Responsabilidade atual |
|---|---|
| `web_assets/index.html` | SPA local: Agentes, World, Automações, Workflows, Approvals, Eventos, Runs, Modelos, Tools, Empresas e Sobre |
| `web.py` | API HTTP local, integração da UI e operações administrativas |
| `execution_service.py` | kernel único de execução e herança segura de skill sets |
| `tool_registry.py` | catálogo de MCPs, nativas e skill sets; aliases, scopes e metadados de herança |
| `skills/*/SKILL.md` | instruções operacionais carregadas apenas quando a skill está ativa |
| `spreadsheet_tools.py` | runtime nativo da skill Excel `.xlsx` |
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
5. Skill sets adicionam conhecimento operacional + tools determinísticas sem criar um novo harness.
6. Conversation, Run, Automation e Workflow Run são entidades diferentes.
7. Empresa/setor/projeto são espaços de contexto e organização; não são threads de conversa.
8. Memória conversacional e contexto organizacional são sistemas separados.
9. Approvals concedem scopes; não devem ser confundidos com a simples existência de uma tool no catálogo.
10. Herança é opt-in por tool (`inheritable=true`); MCPs sensíveis não são herdados automaticamente.

## Skill sets herdáveis

Skill sets ficam no mesmo catálogo exibido em **Tools**, mas usam `kind: skill-set`. Cada skill pode fornecer um `SKILL.md` e um runtime nativo/MCP.

Quando um agente delega para outro (`parent_agent_id`), apenas tools explicitamente marcadas como `inheritable=true` entram no conjunto efetivo do agente filho. Os scopes herdados seguem uma regra mais restritiva:

- somente permissões **permanentes** do agente pai podem acompanhar a skill;
- approvals temporários/dinâmicos do pai **não** são herdados;
- o filho continua podendo solicitar seu próprio approval quando faltar scope.

### Excel Spreadsheet

`skill_excel` é o primeiro skill set herdável do ACTIS GEN.

```text
skill_excel
├─ spreadsheet.read
└─ spreadsheet.write
```

Capacidades atuais:

- criar `.xlsx`;
- inspecionar workbook, abas e ranges;
- editar células, ranges, linhas e colunas;
- fórmulas;
- formatação e number formats;
- Excel Tables;
- gráficos bar/line/pie;
- validação estrutural antes da entrega.

A implementação usa `openpyxl` e mantém as fórmulas no workbook. Como `openpyxl` não calcula resultados de fórmulas, o arquivo é marcado para recalcular quando aberto no Excel/LibreOffice.

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
spreadsheet.read
spreadsheet.write
actis.admin
```

Um agente pode ter tools declaradas e uma lista permanente de `permissions`. Approvals aprovados adicionam scopes dinamicamente. Browser, Files e Computer filtram as operações MCP reais por scope. Excel filtra leitura e mutação do workbook por `spreadsheet.read`/`spreadsheet.write`.

## Concorrência

- Runs independentes podem existir em paralelo.
- O chat global impede duas requisições simultâneas para **o mesmo agente**.
- Agentes diferentes podem responder simultaneamente.
- Harness Computer é serializado: uma segunda execução com Computer é recusada enquanto outra está ativa.

## Estado do repositório

A documentação deve acompanhar o estado real do repositório. Features novas que alterem capabilities, herança, execução ou comportamento do General devem atualizar esta arquitetura no mesmo conjunto de mudanças.
