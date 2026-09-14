# meuharness

General-purpose agent harness built as a thin, stable layer on top of the Microsoft Agent Framework (MAF).

## Architecture

```text
Domain harnesses / apps
        |
        v
+---------------------------+
|      meuharness core      |
| router / capabilities     |
| policies / events         |
| verification / contracts  |
+-------------+-------------+
              |
              v
+---------------------------+
|        MAF adapter        |
+-------------+-------------+
              |
              v
+---------------------------+
| Microsoft Agent Framework |
+---------------------------+
```

## Repository blocks

- `upstream/agent-framework/` — pinned Microsoft Agent Framework upstream source (git submodule).
- `src/meuharness/core/` — our framework-independent core contracts.
- `src/meuharness/adapters/maf/` — the only layer allowed to depend directly on MAF.
- `src/meuharness/domains/` — domain-specific harnesses such as ACTIS, coding and research.
- `docs/` — architecture decisions and block-by-block implementation plan.
- `tests/` — contract, adapter and integration tests.

## Dependency rule

`domains -> meuharness core -> adapter interface -> MAF adapter -> Microsoft Agent Framework`

The core must never import domain code, and domains must not import MAF directly.

## Current status

**Block 0 — Foundation**: repository structure + pinned MAF upstream.
