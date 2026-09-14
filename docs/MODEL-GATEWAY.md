# Model gateway

9Router is an external local model/provider gateway. It exposes the
OpenAI-compatible endpoint `http://127.0.0.1:20128/v1` and owns upstream connections,
OAuth credentials, provider routing and fallback.

The harness receives a gateway URL, gateway API key and explicit model ID. It
does not read provider tokens or reproduce authentication logic. OpenRouter may
exist behind 9Router as a provider, but it is not a direct harness dependency.

## Implemented boundary

`NineRouterSettings` validates configuration without displaying its key.
`NineRouterGateway.open_client()` creates the SDK client consumed by the MAF
adapter. `list_models()` and `complete()` are independent diagnostic operations.
Application execution remains `Harness.run -> MAFRuntime -> MAF -> 9Router`.

Every SDK client has an explicit timeout and no automatic retries. The harness
additionally limits overall execution. Error classification inspects exception
causes and HTTP status, never provider-specific message substrings.

## Model selection

Use the returned `/v1/models` IDs exactly. No model is guessed or silently selected
in code. Catalog presence does not guarantee that the connected account is
authorized or has quota. Test direct completion before attributing a failure to
MAF. The CLI's `--model` option makes an explicit temporary override.

A fresh installation may leave the default model blank while using
`meuharness-smoke --list-models`; execution requires a selected model.
The tested local default and provider incident are recorded in
`docs/DIAGNOSTIC-2026-09-14.md`, not hardcoded into the library.

## Configuration precedence

1. CLI model/timeout overrides for the diagnostic invocation.
2. Process environment.
3. Explicit `.env` file, or current directory `.env` when omitted.
4. Built-in gateway URL and 30-second timeout defaults.

The API key has no built-in default. Only `./.env` is considered automatically;
the library does not search parent directories for another application's secrets.
