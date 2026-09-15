# Local diagnostic — 2026-09-14

> **Documento histórico.** Este arquivo registra o diagnóstico de 2026-09-14 e não descreve o estado atual completo do ACTIS GEN. Para o estado corrente, veja `ACTIS-GEN-ESTADO-ATUAL.md`.

## Verified environment

- Device: `lowkanta-H310`, accessed with Remote Desktop Commander.
- Repository: `/home/lowkanta/meuharness`, initially clean at `c63ee2f` on `main`.
- `9router.service`: active/running. Listener: `127.0.0.1:20128` only.
- The remote command session needed the existing user bus environment to query
  systemd; the initial bus error did not indicate a service outage.

No API keys or OAuth tokens are included in this record.

## Progressive diagnosis

| Layer | Evidence | Conclusion |
|---|---|---|
| Configuration | `NINEROUTER_MODEL=gc/gemini-2.5-flash`; gateway key present | Configuration loaded; model selected explicitly |
| Catalog | `GET /v1/models`: HTTP 200, 510 IDs, selected ID present | Gateway reachable; existence does not prove provider availability |
| Direct Gemini request | HTTP 503 body reported upstream HTTP 403 | Failure already occurred without MAF |
| Gateway logs | Gemini onboarding completed without `project_id`; generation returned 403; account/model cooled down for 120 s | Gemini provider/account path needs attention inside 9Router; root cause of the missing project/entitlement remains unverified |
| Direct alternate route | Catalog-exposed `cx/gpt-5.6-luna`: HTTP 200, 2.08 s | An existing provider path is operational |
| Initial MAF probe | Exact requested reply, 3.09 s | MAF → gateway → provider works |
| New staged smoke | Direct 1.74 s; `Harness.run` through MAF 1,858.71 ms; both matched the same new random probe | New production execution slice works against the real gateway |

The earlier "hang" was not reproduced as a MAF deadlock. Its contributing factors
were a failing provider path, no progress messages, and SDK defaults observed in
the installed source: a 600-second read timeout and two automatic retries. The new
code makes stage, deadline and failure classification explicit.

## Dependency correction

Core 1.18.0 and OpenAI provider 1.14.3 initially resolved to editable source under
`upstream/agent-framework/python/packages/`. The same published versions were
available and were installed from packages instead. Import paths were verified
under `.venv/lib/python3.12/site-packages/`. OpenAI SDK 3.13.0 was retained.

## Scope of the fix

The application default is changed to the tested `cx/gpt-5.6-luna` route, with the
previous `.env` retained in an ignored private backup. This makes the harness
usable but does not claim that Gemini account authorization has been repaired.
No provider authentication details were moved into the harness and no gateway
fallback policy was changed. The upstream submodule was not edited.

## Verification

The repository contains deterministic tests for the neutral API, invalid input,
timeouts/cancellation, sanitized errors, model discovery, real MAF/SDK HTTP
translation, no automatic SDK retry, context isolation and import boundaries.
The full live diagnostic additionally requires a fresh nonce match at both direct
and MAF stages, avoiding success based only on HTTP status or catalog presence.

Use `python -m pytest -q`, `ruff check src tests`, and `meuharness-smoke` for current
results. Timings above are observed samples, not a latency guarantee.


Final validation also built a wheel, installed it into a fresh isolated virtualenv,
verified MAF imports from published site-packages, and ran the live smoke from
outside the repository using an explicit env path. That clean installation
completed direct generation in 1.56 s and MAF generation in 1,491.72 ms.
The original environment passed dependency consistency checks. The final suite
contains 36 tests, including a mismatched-probe regression that must never emit
success for an unverified reply.
