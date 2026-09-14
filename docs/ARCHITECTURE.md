# Architecture

Generalize infrastructure; specialize knowledge, state, tools and business rules.

## Current execution path

The application creates a `HarnessRequest` and calls `Harness.run`. The core applies
a deadline and records lifecycle events. `RuntimeAdapter` is the replaceable
boundary; today its implementation is `MAFRuntime`. MAF uses a fresh
OpenAI-compatible client created by the configured gateway. 9Router owns provider
authentication, model routing and its own fallback policy.

| Layer | Current responsibility |
|---|---|
| Calling software/domain | Task, context, instructions, operational state and UX |
| `core` | Request/result/error contracts, deadline, correlation and lifecycle |
| `adapters/maf` | MAF agent creation, message/options translation, runtime lifecycle |
| `providers/nine_router` | Gateway settings, SDK factory, catalog/direct diagnostic requests |
| `providers/openai_errors.py` | Shared SDK exception classification without raw bodies |
| `bootstrap.py` | Explicit composition of the core, chosen runtime and gateway |
| External 9Router | Provider OAuth/API credentials, upstream routing and fallback |

Only `Harness.run` is implemented in the public execution API. The global router,
registry, persistent state, tool policies, approvals and verification workflows
remain future blocks. The smoke CLI adds a narrow connectivity verifier.

## Dependency rules

1. Core never imports domains, MAF, provider SDKs or concrete gateways.
2. Domains use public contracts; they never import MAF directly.
3. All production MAF imports remain inside `adapters/maf`.
4. 9Router-specific settings and HTTP access belong in `providers/nine_router`.
5. SDK-specific client factory types stay outside core; avoid an abstraction whose
   public return type exposes a vendor SDK.
6. Provider OAuth sessions and upstream keys remain inside 9Router. The application
   receives only the gateway key and does not inspect its provider database.
7. The upstream submodule is a reference. Application runtime dependencies are
   reviewed published packages, not editable submodule installations.
8. Business/domain vocabulary does not belong in general infrastructure.

## Two routing responsibilities

The future **global router** chooses domain, capability and execution path.
The existing **9Router gateway** chooses model/provider routes according to its
configuration. These are different responsibilities.

## ACTIS boundary

ACTIS is a separate software product. It captures WhatsApp messages, manages its
local database and operational state, assembles context, then asks the harness to
perform a structured task. The harness does not capture WhatsApp or own ACTIS
customers, orders, payments or Kanban rules.

The IDE/ChatGPT workflow is used to develop this infrastructure. Building a coding
agent, Codex CLI wrapper or IDE orchestrator is outside the current scope.
