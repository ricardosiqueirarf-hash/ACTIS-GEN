# Model Gateway

## Decision

OpenRouter is the default model gateway for `meuharness`.

It is responsible for model/provider access and routing. It is **not** the harness Global Router.

```text
software/domain
      |
      v
meuharness Global Router
      |
      v
MAF runtime / workflow
      |
      v
ModelGateway
      |
      v
OpenRouter
      |
  +---+---+---+
  v   v   v   v
OpenAI Anthropic Google ...
```

## Responsibilities

### OpenRouter

- unified model API;
- model/provider routing;
- provider failover;
- model catalog;
- usage/cost visibility;
- OpenRouter credentials and supported BYOK provider credentials.

### meuharness Global Router

- domain selection;
- capability selection;
- execution-path selection;
- runtime/agent/tool selection;
- policy-aware routing.

### MAF

- agent and workflow execution;
- tool loops;
- middleware;
- checkpoints;
- HITL;
- runtime lifecycle.

## Authentication boundary

The harness uses `OPENROUTER_API_KEY` as the default model-gateway credential.

Provider BYOK credentials may be configured in OpenRouter where supported. Provider subscription OAuth sessions (for example a separate ChatGPT or Claude consumer subscription login) must not be assumed to be available through OpenRouter unless the provider/OpenRouter explicitly supports that credential flow.

## External agent runtimes

Codex CLI is an external agent runtime, not a model provider. It may be integrated through a dedicated adapter. Codex CLI can also be configured to use OpenRouter as its model provider, allowing its model traffic to share the same OpenRouter gateway.

## First target

The first functional vertical slice should be:

```text
HarnessRequest
    -> MAF adapter
    -> OpenRouter ModelGateway
    -> selected model
    -> normalized HarnessResult
```

Only after this path is tested should automatic domain/capability routing be added.
