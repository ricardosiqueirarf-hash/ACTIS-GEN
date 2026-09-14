# 9Router gateway boundary

`NineRouterSettings` reads and validates the gateway endpoint, gateway API key,
model route and timeout. The key is excluded from settings repr. An empty model
is allowed for catalog discovery, but completion requires explicit selection.

`NineRouterGateway` creates SDK clients for MAF and provides `list_models()` and
`complete()` for direct diagnosis. The direct path is diagnostic; application
execution goes through `Harness` and the runtime adapter.

Provider OAuth credentials, access tokens and provider-specific configuration
stay inside 9Router. No 9Router configuration database is read or modified here.
The gateway default remains `http://127.0.0.1:20128/v1`.
