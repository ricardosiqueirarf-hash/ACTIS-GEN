# meuharness

General-purpose agent harness built as a thin, stable layer on top of the Microsoft Agent Framework (MAF), with OpenRouter as the default model gateway.

## Architecture

```text
Software / domain features
        |
        v
+---------------------------+
|      meuharness core      |
| global router             |
| capabilities / policies   |
| contracts / verification  |
+-------------+-------------+
              |
              v
+---------------------------+
|        MAF adapter        |
| agents / workflows        |
| tools / HITL / runtime    |
+-------------+-------------+
              |
              v
+---------------------------+
|     model gateway         |
|       OpenRouter          |
+-------------+-------------+
              |
      +-------+-------+
      |       |       |
      v       v       v
   OpenAI  Anthropic Google ...
```

External agent runtimes such as Codex CLI may also be attached through dedicated adapters. Codex can itself be configured to use OpenRouter as its model provider.

## Responsibility split

- **Global Router (ours)** — decides domain, capability and execution path.
- **MAF** — agent/workflow runtime, tool loops, checkpoints, middleware, HITL and orchestration primitives.
- **OpenRouter** — default model gateway: model/provider selection, failover, unified API and model usage visibility.
- **Domains/apps** — own business context and software-specific features.

## Repository blocks

- `upstream/agent-framework/` — pinned Microsoft Agent Framework upstream source (git submodule).
- `src/meuharness/core/` — our framework-independent core contracts.
- `src/meuharness/adapters/maf/` — the only layer allowed to depend directly on MAF.
- `src/meuharness/providers/openrouter/` — OpenRouter model-gateway integration boundary.
- `src/meuharness/domains/` — domain-specific harnesses such as ACTIS, coding and research.
- `docs/` — architecture decisions and block-by-block implementation plan.
- `tests/` — contract, adapter and integration tests.

## Dependency rule

`software/domain -> meuharness core -> runtime adapter -> MAF -> model gateway -> OpenRouter`

The core must never import domain code. Domains must not import MAF directly. OpenRouter is a model gateway, not the global/domain router.

## Current status

**Block 0 — Foundation**: repository structure + pinned MAF upstream + OpenRouter selected as default model gateway.
