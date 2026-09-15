# ACTIS GEN — 9Router e modelos

## Papel do 9Router

9Router é o gateway local OpenAI-compatible e permanece separado do ACTIS GEN. Na máquina auditada:

```text
http://127.0.0.1:20128/v1
```

Responsabilidades do 9Router:

- autenticação de providers;
- catálogo de modelos;
- routing/fallback upstream;
- tradução OpenAI-compatible;
- estado/cooldown de providers.

Responsabilidades do ACTIS GEN:

- escolher o ID configurado no agente;
- criar Runs;
- executar pelo MAF;
- observar saúde e expor diagnóstico.

## Settings

`NineRouterSettings` recebe URL, gateway key, modelo e timeout. A key não deve aparecer na UI ou em documentação/logs.

## Diagnóstico operacional

### Probe independente

```text
POST /api/model-probe
```

Faz uma completion direta pelo gateway com a instrução `Reply exactly ACTIS_PROBE_OK`. Esse teste não depende da sessão administrativa do dashboard.

### Diagnóstico de providers

`POST /api/provider-connections/test` usa o dashboard nativo do 9Router e pode depender da sessão autenticada do navegador. É diagnóstico administrativo, não requisito para o funcionamento normal dos agentes.

## Catálogo e saúde

`GET /api/models` expõe catálogo do 9Router. `GET /api/model-health` combina informação observável disponível no ambiente. Presença no catálogo não garante entitlement/quota.

A UI distingue modelos observados funcionando, falhando e não observados. Essas classes são estado operacional observado, não benchmark de inteligência.

## Sem fallback no ACTIS

O ACTIS GEN não escolhe automaticamente outro modelo quando uma Run falha. Qualquer fallback de provider/modelo pertence à configuração do 9Router.

## Registro histórico

O incidente Gemini e a validação inicial de `cx/gpt-5.6-luna` permanecem documentados em `DIAGNOSTIC-2026-09-14.md` como fotografia histórica, não como descrição do estado corrente.
