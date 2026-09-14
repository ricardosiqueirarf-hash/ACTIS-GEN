# Microsoft Agent Framework integration

All direct production MAF imports live in `src/meuharness/adapters/maf/`.
Public core/domain contracts do not expose framework-specific classes.

## Actual runtime dependencies

The first verified package set is:

- `agent-framework-core==1.18.0`
- `agent-framework-openai==1.14.3`
- `openai==3.13.0`

These are published stable packages. Separate MAF packages have independent
versions; matching the core and provider version numbers is not required.
The broad `agent-framework` metapackage includes integrations this slice does not use.
Upgrades must be deliberate and pass adapter tests plus the real gateway smoke test.

MAF receives an explicit `AsyncOpenAI` client with gateway credentials/base URL,
an HTTP timeout and `max_retries=0`. Each run owns and closes its client and agent.
The harness additionally imposes an end-to-end async deadline. MAF's
`OpenAIChatCompletionClient` converts messages/options and normalizes the response.

Sources: [Microsoft provider integration](https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/openai),
[published core package](https://pypi.org/project/agent-framework-core/1.18.0/),
[published provider package](https://pypi.org/project/agent-framework-openai/1.14.3/).
Constructor signatures and option names were also checked against installed code.

## Reference upstream

`upstream/agent-framework` remains pinned at
`926b4ecacdaa95cb909c9d08f77b4fd898f273c0`.
It is a source reference, not an editable runtime dependency. Do not install core
or OpenAI packages with `pip install -e upstream/...` in the application environment.
Updating the reference pin and upgrading runtime packages are separate operations.
