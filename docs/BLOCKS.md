# Implementation blocks

We will evolve the harness in isolated blocks. A block only starts after the previous block has explicit contracts and tests.

## Block 0 — Foundation

Goal: establish repository boundaries, pin Microsoft Agent Framework (MAF) as upstream infrastructure and select OpenRouter as the default model gateway.

Deliverables:
- MAF pinned under `upstream/agent-framework` as a git submodule.
- OpenRouter selected as default model gateway.
- Clear dependency direction.
- Minimal Python package skeleton.
- Architecture documentation.

No domain logic belongs in Block 0.

## Block 1 — Contracts

Define framework-independent interfaces used by all later blocks:
- `HarnessRequest`
- `HarnessResult`
- `ExecutionContext`
- `Capability`
- `Domain`
- `Tool`
- `PolicyDecision`
- `VerificationResult`
- runtime adapter protocol
- model gateway protocol
- model request/result contracts

Rule: public core contracts must not import MAF or OpenRouter-specific classes.

## Block 2 — Model Gateway

Build the first vertical integration through OpenRouter:
- `ModelGateway` implementation for OpenRouter;
- `OPENROUTER_API_KEY` configuration;
- explicit model selection;
- optional OpenRouter router/auto selection;
- normalized model results/errors;
- first end-to-end test through MAF -> OpenRouter -> model.

OpenRouter routes models/providers only. It does not replace the harness Global Router.

## Block 3 — Capability and Domain registries

Domains declare capabilities. The general harness discovers capabilities without hardcoding ACTIS, research or any future operational domain.

## Block 4 — Global Router

Route request -> domain -> capability -> execution path. Domain-local routers remain inside each domain. Model selection may then be delegated to the OpenRouter model gateway.

## Block 5 — State and Context

Sessions, execution context, state references, memory providers and context assembly.

## Block 6 — Tools and Execution

Tool registry, validated execution, idempotency and normalized results.

## Block 7 — Policies and Autonomy

Global policy pipeline, domain policies, approvals and autonomy levels.

## Block 8 — Verification and Recovery

`plan -> act -> verify -> recover`, including retries, fallback and semantic verification contracts.

## Block 9 — Events and Audit

Domain-neutral event envelope, event bus adapter and complete execution audit trail.

## Block 10 — First real domain

ACTIS becomes the first domain implementation without leaking business concepts into the general core.

## Explicitly out of scope

Coding-agent orchestration, Codex CLI integration and IDE automation are not part of the current `meuharness` roadmap. Development is performed externally through the user's IDE / ChatGPT workflow.
