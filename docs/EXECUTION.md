# Execution slice — 0.1.0

## Decision

Build one useful vertical slice before declaring speculative Domain, Tool, Policy,
State or Approval interfaces. The public surface currently consists of `Harness`,
`HarnessRequest`, `HarnessResult`, `HarnessError`, `RuntimeAdapter` and `TokenUsage`.

The runtime adapter protocol returns a neutral `RuntimeResult`. MAF and SDK types
never appear in the core. The MAF-specific OpenAI-compatible client factory protocol
stays inside its adapter: putting an SDK-returning ModelGateway protocol in core
would make the apparent abstraction provider-dependent.

## Request and result

| Field | Meaning |
|---|---|
| `prompt` | Non-empty task supplied by the calling software |
| `context` | JSON object; copied at construction and serialized into the user input |
| `instructions` | Optional instructions supplied by the trusted application |
| `request_id` | Correlation ID; generated if omitted |
| `timeout_s` | Finite, positive execution deadline, default 30 seconds |
| `max_output_tokens` | Output budget, default 256, translated to MAF `max_tokens` |

Results contain `request_id`, `text`, `model`, `runtime`, `elapsed_ms` and `usage`.
`model` is the requested gateway route, not proof of which upstream model a 9Router
fallback used. Usage reflects SDK/framework reports; missing counts remain `None`.
Invalid request data fails during construction, before contacting the runtime.

Every run creates a separate client and agent. No history, implicit session or
business state is shared. Context belongs to the caller; do not mutate a request
while it is running. A correlation ID is **not** an idempotency key: calling `run`
again makes another request even when the ID is reused.

## Failures

| Code | Interpretation |
|---|---|
| `configuration` | Missing/invalid gateway configuration |
| `model_not_listed` | Smoke-selected model absent from the catalog |
| `authentication` | Gateway/provider returned 401 or 403 |
| `not_found` | Gateway endpoint/model returned 404 |
| `rate_limited` | Gateway/provider returned 429 |
| `gateway_error` | Gateway returned a server error; inspect gateway logs |
| `request_rejected` | Other HTTP request rejection |
| `connection` | Cannot reach the configured endpoint |
| `timeout` | HTTP or execution deadline exceeded |
| `invalid_response` | Missing usable text or invalid catalog |
| `runtime_error` | Unexpected runtime/adapter failure |
| `verification` | Smoke reply did not match its random probe |

MAF may wrap SDK exceptions. The adapter walks their causes to preserve status and
classification. Public errors never repeat raw upstream bodies or credentials.
In particular, an HTTP 503 containing an upstream 403 remains a `gateway_error`
with `status_code=503`; diagnosing the provider cause belongs in 9Router logs.

No SDK automatic retry is enabled. 9Router retains its own configured fallback
behavior. `asyncio.timeout` cancels a run; caller cancellation propagates unchanged.
Cancellation stops waiting locally and does not imply that a provider rolled back
or did not process the request. Future tools need their own execution/idempotency
policies before retries can be enabled safely.

## Observability

Logger `meuharness` emits `harness.run.started` and exactly one terminal lifecycle
event: `completed`, `failed` or `cancelled`. Log records include correlation ID,
runtime and, when relevant, elapsed milliseconds/error code. The application owns
logging handlers and JSON formatting. The core does not log prompt, context, key,
raw exception bodies or model responses.

The CLI emits JSON lines and prints only its synthetic connectivity replies. A
successful catalog lookup is not evidence of provider entitlement or health.

## Boundaries still to build

No tools, file access, shell execution, messaging, approvals, domain dispatch or
persistent state are attached to these agents. ACTIS supplies a structured task
when it needs intelligence and continues to own capture, operational data and UX.
