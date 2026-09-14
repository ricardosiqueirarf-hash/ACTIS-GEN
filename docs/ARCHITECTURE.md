# Architecture

## Layering

```text
+--------------------------------------------------+
| Software / domain features                       |
| ACTIS / research / future operational apps       |
+-------------------------+------------------------+
                          |
                          v
+--------------------------------------------------+
| meuharness general core                          |
| contracts / global router / capabilities         |
| policies / events / verification / state refs    |
+-------------------------+------------------------+
                          |
                          v
+--------------------------------------------------+
| Runtime adapter boundary                         |
| `adapters/maf` today; other runtimes may exist   |
+-------------------------+------------------------+
                          |
                          v
+--------------------------------------------------+
| Microsoft Agent Framework                        |
| agents / workflows / harness / middleware / HITL |
| tools / checkpoints / runtime                    |
+-------------------------+------------------------+
                          |
                          v
+--------------------------------------------------+
| Model gateway boundary                           |
| OpenRouter is the default gateway                |
+-------------------------+------------------------+
                          |
             +------------+------------+
             |            |            |
             v            v            v
          OpenAI       Anthropic     Google ...
```

Software development through IDEs and ChatGPT is intentionally outside this architecture. `meuharness` exists for agent execution inside software products and operational domains, not for coding automation.

## Router responsibilities

There are two different routing layers and they must not be conflated:

1. **Global Router — owned by meuharness**
   - chooses domain;
   - chooses capability;
   - chooses execution path/runtime;
   - decides whether a task should use a model, workflow or tool.

2. **Model Router/Gateway — OpenRouter**
   - exposes a unified model API;
   - selects/serves models and upstream providers;
   - handles provider routing/failover according to OpenRouter configuration;
   - centralizes model usage visibility and provider credentials supported by OpenRouter.

OpenRouter does not replace domain routing or software-specific behavior.

## Non-negotiable dependency rules

1. `core` never imports a domain.
2. `core` never imports Microsoft Agent Framework directly.
3. Domains depend only on public `meuharness` contracts and domain-owned code.
4. Direct MAF imports are restricted to `src/meuharness/adapters/maf/` and MAF-specific integration tests.
5. OpenRouter-specific code is restricted to `src/meuharness/providers/openrouter/` and provider integration tests.
6. Upstream MAF source is read-only from our architecture's point of view. Changes to MAF must be made upstream or in our adapter, not as hidden local edits.
7. Business/domain nouns do not belong in the general core.
8. Coding-agent responsibilities do not belong in the general core or in a dedicated coding domain unless scope changes explicitly in the future.

## Why MAF is a submodule

The Microsoft repository is intentionally not copied into our source tree. Pinning it as a submodule gives us:

- exact upstream commit reproducibility;
- clean ownership boundaries;
- simple upstream updates;
- no accidental mixing of Microsoft code with our own;
- the ability to replace the runtime adapter in the future.

## Ownership

### MAF owns

Agent/runtime primitives, workflows, checkpoints, middleware execution, tool calling primitives, human-in-the-loop primitives and runtime lifecycle.

### OpenRouter owns

Default model gateway responsibilities: unified model endpoint, model/provider routing, failover and model-usage visibility.

### meuharness core owns

Stable runtime-independent contracts, global routing, capability discovery, global policy composition, domain registry, event contracts and semantic verification interfaces.

### domains/apps own

Business/domain state, vocabulary, domain tools, domain routing details, domain policies, domain verifiers, domain-specific prompts/agents and software features that collect or organize domain context.

### external development workflow owns

IDE usage, repository editing, ChatGPT-assisted coding and other software-development activities. These are consumers/builders of the software, not responsibilities of `meuharness`.
