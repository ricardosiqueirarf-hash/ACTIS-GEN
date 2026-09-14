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
- runtime/tool selection;
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

Provider BYOK credentials may be configured in OpenRouter where supported. Provider subscription OAuth sessions must not be assumed to be available through OpenRouter unless explicitly supported by the provider/OpenRouter credential flow.

## Coding is outside this layer

`meuharness` does not orchestrate Codex CLI or other coding agents in the current scope. Software development is performed externally through the user's IDE / ChatGPT workflow. OpenRouter exists here only as the model gateway for software agents and operational domains that run through `meuharness`.

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
