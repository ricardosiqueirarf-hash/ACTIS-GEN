# OpenRouter provider

This directory owns OpenRouter-specific model-gateway integration.

It may depend on OpenRouter API details. Public `meuharness.core` contracts must not.

Planned responsibilities:

- read `OPENROUTER_API_KEY` from configuration;
- translate generic `ModelRequest` contracts to OpenRouter requests;
- normalize responses and errors;
- support explicit model selection and OpenRouter routing modes;
- expose model/provider metadata needed by the harness without leaking provider-specific types into core.

This provider does not perform domain or capability routing.
