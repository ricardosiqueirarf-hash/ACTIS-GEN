# ACTIS Config — Connectors

## Objetivo

ACTIS Config é o registry central de integrações do ACTIS GEN.
Ele separa a conexão com softwares da definição individual dos agentes.

```text
Agente
  ↓ permissão
ACTIS Connector Registry
  ├─ 9Router
  ├─ ACTIS Agent Bus
  └─ HTTP APIs / softwares externos
```

A interface fica em **Sistema → Config**.
A persistência usa a collection `connectors` no SQLite canônico do ACTIS.

## Princípios

- secrets não são gravados no SQLite;
- o registry guarda apenas `credential_ref`;
- acesso pode ser global (`*`) ou limitado por agente;
- conectores built-in não podem ter parâmetros críticos sobrescritos pela UI;
- cada connector declara suas capabilities;
- teste de saúde é separado da configuração.

## Built-ins

### 9Router

Usa a configuração runtime existente (`NINEROUTER_*`).
O botão **Testar** consulta o catálogo real de modelos via `NineRouterGateway`.
O connector controla visibilidade/acesso, mas não substitui o provider layer.

### ACTIS Agent Bus

É a camada interna de comunicação agente ↔ agente.
Reutiliza `channels.py` como transporte persistente, evitando um segundo sistema de mensagens.

Agentes com `ACTIS Admin` recebem tools para:

- listar connectors;
- descobrir canais;
- ler histórico de canal;
- publicar mensagens no canal;
- continuar usando delegação direta pelo kernel canônico.

## HTTP API

Para integrar outro software, crie um connector `http` com:

- `base_url` HTTP(S);
- `auth_type`: `none`, `api_key_env` ou `bearer_env`;
- `credential_ref`: nome da variável de ambiente;
- capabilities;
- agentes autorizados.

Exemplo conceitual:

```json
{
  "name": "Meu ERP",
  "kind": "http",
  "base_url": "https://erp.exemplo.com/api/health",
  "auth_type": "bearer_env",
  "credential_ref": "MEU_ERP_TOKEN",
  "agent_ids": ["general", "financeiro"],
  "capabilities": ["customers", "orders"]
}
```

## API local

- `GET /api/connectors`
- `POST /api/connectors`
- `PUT /api/connectors/{id}`
- `DELETE /api/connectors/{id}`
- `POST /api/connectors/{id}/test`

## Próxima extensão natural

O registry já resolve configuração, saúde e autorização. O próximo nível é um adapter/runtime por tipo de connector para transformar capabilities externas em tools padronizadas disponíveis ao agente, sem ensinar o agente a lidar diretamente com detalhes de cada API.
