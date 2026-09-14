# Implementation blocks

## Completed: foundation and first execution slice

- Reference upstream submodule and documented responsibility boundaries.
- Published MAF core/OpenAI packages, independent of the submodule checkout.
- Minimal request/result/error/usage contracts and runtime adapter protocol.
- `Harness.run` with deadline, cancellation and lifecycle logging.
- MAF adapter and 9Router client/configuration boundary.
- Catalog discovery, direct completion and MAF smoke stages.
- Deterministic contract and real-adapter HTTP-fixture tests.

This combines the useful portions of the previous Contracts and Local Model
Gateway blocks. Unused interface placeholders were deliberately deferred.

## Next: capability registry and explicit routing

Add a small registry where an external domain registers a named capability and its
handler. Route by an explicit domain/capability identifier first. Required tests:
duplicate registration, unknown capability, isolation between domains and stable
error results. Only introduce model-based routing after a real use case needs it.

Do not create ACTIS business rules inside core or make WhatsApp an input transport
of the harness. ACTIS owns capture and business state and calls the public contract.

## Subsequent blocks, driven by real consumers

1. State/context references and explicit session boundaries.
2. Validated tool execution, side-effect classification and idempotency.
3. Policy decisions and human approvals for critical actions.
4. Verification/recovery with bounded retry rules and observable outcomes.
5. Durable events/audit storage when lifecycle logging is insufficient.
6. First ACTIS integration using a real structured task.

Each block needs a concrete consumer, small contracts and tests. Coding-agent
orchestration, Codex CLI and IDE automation remain outside this roadmap.
