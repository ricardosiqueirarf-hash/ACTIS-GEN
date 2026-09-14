# Domains

Domains own business vocabulary, task context, policies and state. They call the
public `meuharness` API and never import MAF directly.

ACTIS is a separate software product and the likely first consumer. It captures
WhatsApp information, organizes operational state and sends structured tasks to
the harness. WhatsApp capture, ColorGlass rules and product UX do not belong in core.

No domain registry or concrete domain implementation exists in this slice.
Coding-agent orchestration is outside the current project scope.
