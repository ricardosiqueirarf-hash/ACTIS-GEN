# Domain harnesses

Each domain is isolated behind the general harness contracts.

Planned examples:

- `actis/` — business operations, customers, orders, payments, production, messaging.
- `coding/` — repositories, files, tests, builds, Git workflows.
- `research/` — web, documents, evidence, citations and synthesis.

A domain may define its own agents, local router, tools, policies, verifiers and state schemas. It must not import Microsoft Agent Framework directly.
