# 9Router provider gateway

This directory owns the `meuharness` integration boundary with the local 9Router service.

9Router is treated as an external local gateway, not as a library embedded in the harness.

Default API boundary:

```text
http://127.0.0.1:20128/v1
```

Planned responsibilities:

- read `NINEROUTER_BASE_URL`, `NINEROUTER_API_KEY` and optional `NINEROUTER_MODEL` from configuration;
- talk to 9Router through its OpenAI-compatible API;
- translate generic `ModelRequest` contracts to gateway requests;
- normalize responses and errors;
- expose model/provider metadata needed by the harness without leaking 9Router-specific types into core.

Provider OAuth sessions and upstream API keys are configured in 9Router, not in `meuharness` core.

OpenRouter may be configured behind 9Router as an optional upstream provider. The harness does not depend on OpenRouter directly.

This gateway does not perform domain or capability routing.
