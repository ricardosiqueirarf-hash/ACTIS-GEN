# ACTIS GEN — API HTTP local

Base padrão: `http://127.0.0.1:8765`.

A API é local e não possui autenticação/RBAC nesta versão. Não exponha a porta publicamente sem adicionar uma camada de segurança.

## Leitura

| Endpoint | Retorno |
|---|---|
| `GET /api/models` | catálogo de modelos |
| `GET /api/model-health` | saúde observada |
| `GET /api/provider-connections` | conexões/providers visíveis |
| `GET /api/tools` | catálogo de tools/scopes |
| `GET /api/agents` | agentes |
| `GET /api/companies` | empresas |
| `GET /api/sectors` | setores |
| `GET /api/context-items` | contexto, filtros `scope_type`/`scope_id` |
| `GET /api/projects` | projetos, opcional `company_id` |
| `GET /api/agent-context-bindings` | bindings, opcional `agent_id` |
| `GET /api/conversations` | conversas, opcional `agent_id` |
| `GET /api/conversations/:id` | conversa individual |
| `GET /api/automations` | automações |
| `GET /api/automations/:id` | automação |
| `GET /api/approvals` | approvals, opcional `status` |
| `GET /api/workflows` | workflows |
| `GET /api/workflows/:id` | workflow + erros de validação |
| `GET /api/workflows/:id/runs` | histórico de workflow runs |
| `GET /api/memory?agent_id=...&q=...` | memórias recuperadas |
| `GET /api/storage` | backend/path do storage |
| `GET /api/runs` | Runs, opcional `agent_id` |
| `GET /api/events` | Event Bus, filtros `limit`, `type`, `run_id` |

## Escrita / ações

| Endpoint | Ação |
|---|---|
| `POST /api/runs/:id/cancel` | cancela Run ativa/órfã |
| `POST /api/provider-connections/test` | teste administrativo de providers |
| `POST /api/model-probe` | completion real de probe |
| `POST /api/approvals` | cria approval |
| `POST /api/approvals/:id/resolve` | aprova/nega |
| `POST /api/approvals/:id/revoke` | revoga approval aprovado |
| `POST /api/workflows` | cria workflow |
| `POST /api/workflows/:id/run` | executa workflow |
| `POST /api/workflows/:id/validate` | valida workflow salvo |
| `POST /api/automations` | cria automação |
| `POST /api/agents` | cria agente |
| `POST /api/context-items` | cria item de contexto |
| `POST /api/projects` | cria projeto |
| `POST /api/conversations` | cria conversa |
| `POST /api/conversations/:id/messages` | envia mensagem persistente |
| `POST /api/companies` | cria empresa |
| `POST /api/sectors` | cria setor |
| `POST /api/agents/:id/chat` | chat direto/global sem conversation persistente |

## Atualização

- `PUT /api/agent-context-bindings/:agent_id`
- `PUT /api/workflows/:id`
- `PUT /api/automations/:id`
- `PUT /api/agents/:id`
- `PUT /api/companies/:id`
- `PUT /api/sectors/:id`

## Exclusão

- `DELETE /api/context-items/:id`
- `DELETE /api/workflows/:id`
- `DELETE /api/automations/:id`

## Concorrência do chat direto

`POST /api/agents/:id/chat` usa um lock por `agent_id`. Se o mesmo agente já estiver atendendo uma requisição direta/global, a API retorna `409`. Isso não bloqueia outro agente.

## Erros

Validações de domínio retornam normalmente `400`; recursos ausentes podem retornar `404`; concorrência do mesmo agente retorna `409`; falhas inesperadas são sanitizadas em `500`.
## ACTIS Config / Connectors

- `GET /api/connectors` — lista registry de integrações.
- `POST /api/connectors` — cria connector.
- `PUT /api/connectors/{id}` — atualiza configuração/acesso.
- `DELETE /api/connectors/{id}` — remove connector não built-in.
- `POST /api/connectors/{id}/test` — executa health/probe da conexão.

Secrets não são retornados nem persistidos; `credential_ref` referencia variável de ambiente.
