# MAF runtime adapter

`MAFRuntime` implements the runtime-independent `RuntimeAdapter` contract. It owns
all MAF imports and translates task/context/options into a fresh MAF `Agent` run.
Its adapter-local gateway protocol creates an OpenAI-compatible SDK client without
putting SDK types in core. SDK error normalization is shared under `providers`.

No business logic, implicit session or tools are installed. See
`docs/EXECUTION.md` and `docs/MAF-INTEGRATION.md` for current behavior and limitations.
