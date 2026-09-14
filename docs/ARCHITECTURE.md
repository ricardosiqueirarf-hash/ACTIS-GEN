# Architecture

## Layering

```text
+--------------------------------------------------+
| Domain harnesses                                 |
| ACTIS / coding / research / future domains       |
+-------------------------+------------------------+
                          |
                          v
+--------------------------------------------------+
| meuharness general core                          |
| contracts / router / capabilities / policies     |
| events / verification / state references         |
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
+--------------------------------------------------+
```

## Non-negotiable dependency rules

1. `core` never imports a domain.
2. `core` never imports Microsoft Agent Framework directly.
3. Domains depend only on public `meuharness` contracts and domain-owned code.
4. Direct MAF imports are restricted to `src/meuharness/adapters/maf/` and MAF-specific integration tests.
5. Upstream MAF source is read-only from our architecture's point of view. Changes to MAF must be made upstream or in our adapter, not as hidden local edits.
6. Business nouns such as `customer`, `order`, `payment`, `inventory`, `commit` or `branch` do not belong in the general core.

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

### meuharness core owns

Stable runtime-independent contracts, global routing, capability discovery, global policy composition, domain registry, event contracts and semantic verification interfaces.

### domains own

Business/domain state, vocabulary, domain tools, domain routing details, domain policies, domain verifiers and domain-specific prompts/agents.
