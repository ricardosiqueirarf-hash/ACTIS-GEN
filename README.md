# meuharness

A small, general-purpose execution layer for software that needs AI. Infrastructure is
shared; business context, vocabulary and state belong to the calling application.

## Working today — 0.1.0

- `await Harness.run(HarnessRequest(...))` returns a structured result.
- Runtime-independent contracts and a replaceable `RuntimeAdapter` protocol.
- A MAF adapter calls the OpenAI-compatible 9Router gateway.
- Explicit model, context, deadline and output budget for each execution.
- Normalized errors, correlation IDs and lifecycle logs without prompt/context/key logging.
- A progressive smoke test isolates configuration, catalog, direct completion and MAF.

This is the first functional execution slice. Domain/capability routing, persistent
state, tools, approvals and recovery workflows are **not implemented yet**.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Set `NINEROUTER_API_KEY` to the **gateway key** from the local 9Router dashboard.
Provider OAuth credentials stay in 9Router. Then discover models and choose one:

```bash
meuharness-smoke --list-models
meuharness-smoke --model '<exact ID returned by the catalog>' --timeout 30
```

After a successful test, set `NINEROUTER_MODEL` in `.env`. The CLI defaults to
`./.env`; from another directory use `--env-file /absolute/path/to/.env`.
Process environment overrides the file. `--model` overrides the selection for that
invocation without changing the file. No model/provider fallback is selected by the harness.

The runtime uses published packages: `agent-framework-core==1.18.0`,
`agent-framework-openai==1.14.3` and `openai==3.13.0`. The broad
`agent-framework` metapackage is unnecessary. The upstream submodule is only a
reference; a fresh install does not require initializing it.

## Application integration

```python
import asyncio

from meuharness import HarnessRequest
from meuharness.bootstrap import create_harness


async def main():
    harness = create_harness()
    result = await harness.run(HarnessRequest(
        prompt="Summarize the supplied record in one sentence.",
        context={"record": {"status": "pending", "missing_fields": ["delivery_date"]}},
        instructions="Use only the supplied context. Do not invent missing values.",
        timeout_s=30,
        max_output_tokens=120,
    ))
    print(result.text)
    print(result.request_id, result.elapsed_ms, result.usage)


asyncio.run(main())
```

`create_harness` is the composition point. Code that already owns a runtime can
construct `Harness(runtime)` directly. Importing the public API does not import MAF.
Catch `HarnessError` and inspect `code`, `retryable` and `status_code` for failures.
`retryable` is a classification, not an instruction to repeat actions automatically.

## Verify

```bash
python -m pytest -q
ruff check src tests
meuharness-smoke --timeout 30
```

Tests use a local HTTP fixture and the actual MAF/SDK. They require no provider
account. The smoke test makes two real model calls and requires your gateway.
Each stage has a deadline; success requires the direct and MAF replies to match a
new random probe. Exit codes: `0` success, `1` diagnostic failure, `130` interruption.

See [architecture](docs/ARCHITECTURE.md), [diagnostic record](docs/DIAGNOSTIC-2026-09-14.md),
[execution contract](docs/EXECUTION.md) and [next blocks](docs/BLOCKS.md).
