# Model Gateway

## Decision

9Router is the default local model/provider gateway for `meuharness`.

It is responsible for provider access, authentication aggregation and model/provider routing behind one local OpenAI-compatible API. It is **not** the harness Global Router.

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
9Router local API
      |
  +---+-------------+----------------+
  v                 v                v
OAuth providers   API-key providers  OpenRouter ...
```

## Responsibilities

### 9Router

- expose a local OpenAI-compatible API;
- keep provider authentication/configuration outside `meuharness`;
- connect OAuth-capable providers supported by 9Router;
- connect API-key providers supported by 9Router;
- expose connected models through a common model API;
- perform model/provider routing and fallback according to its configuration.

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

## Local endpoint boundary

The default gateway endpoint is configured as:

```text
NINEROUTER_BASE_URL=http://127.0.0.1:20128/v1
```

The harness also receives a 9Router API key copied/generated from the local 9Router dashboard:

```text
NINEROUTER_API_KEY=...
```

`meuharness` does not store provider OAuth credentials directly. Those credentials stay behind the 9Router boundary.

## OpenRouter relationship

OpenRouter is no longer the direct gateway used by `meuharness`. If desired, it can be configured as one provider behind 9Router.

The dependency is therefore:

```text
meuharness -> MAF -> 9Router -> provider
```

and optionally:

```text
meuharness -> MAF -> 9Router -> OpenRouter -> upstream model/provider
```

## Coding is outside this layer

`meuharness` does not orchestrate Codex CLI or other coding agents in the current scope. Software development is performed externally through the user's IDE / ChatGPT workflow. 9Router exists here as the model/provider gateway for software agents and operational domains that run through `meuharness`.

## First target

The first functional vertical slice should be:

```text
HarnessRequest
    -> MAF adapter
    -> 9Router ModelGateway
    -> connected model/provider
    -> normalized HarnessResult
```

Only after this path is tested should automatic domain/capability routing be added.
