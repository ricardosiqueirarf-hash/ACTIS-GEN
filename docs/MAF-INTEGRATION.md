# Microsoft Agent Framework integration

`meuharness` uses Microsoft Agent Framework (MAF) as the runtime foundation, but does not expose MAF directly to domains.

## Upstream source

Repository: `microsoft/agent-framework`

Pinned upstream commit for Block 0:

`926b4ecacdaa95cb909c9d08f77b4fd898f273c0`

The source is mounted as the git submodule `upstream/agent-framework`.

## Integration rule

All MAF-specific imports and translations live under:

`src/meuharness/adapters/maf/`

The rest of the project depends on stable `meuharness` contracts rather than Microsoft-specific classes.

## Update strategy

MAF updates are explicit. We move the submodule pointer to a reviewed upstream commit, run adapter/contract tests, and only then accept the update. We do not silently track MAF `main` at runtime.
