# Implementation blocks

We will evolve the harness in isolated blocks. A block only starts after the previous block has explicit contracts and tests.

## Block 0 — Foundation

Goal: establish repository boundaries and pin Microsoft Agent Framework (MAF) as upstream infrastructure.

Deliverables:
- MAF pinned under `upstream/agent-framework` as a git submodule.
- Clear dependency direction.
- Minimal Python package skeleton.
- Architecture documentation.

No routing, policies or domain logic belongs in Block 0.

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

Rule: public core contracts must not import MAF.

## Block 2 — Capability and Domain registries

Domains declare capabilities. The general harness discovers capabilities without hardcoding ACTIS, coding or research concepts.

## Block 3 — Global Router

Route request -> domain -> capability -> execution path. Domain-local routers remain inside each domain.

## Block 4 — State and Context

Sessions, execution context, state references, memory providers and context assembly.

## Block 5 — Tools and Execution

Tool registry, validated execution, idempotency and normalized results.

## Block 6 — Policies and Autonomy

Global policy pipeline, domain policies, approvals and autonomy levels.

## Block 7 — Verification and Recovery

`plan -> act -> verify -> recover`, including retries, fallback and semantic verification contracts.

## Block 8 — Events and Audit

Domain-neutral event envelope, event bus adapter and complete execution audit trail.

## Block 9 — First real domain

ACTIS becomes the first domain implementation without leaking business concepts into the general core.
