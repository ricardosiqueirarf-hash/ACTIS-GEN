# MAF adapter

This directory is the compatibility boundary between `meuharness` and Microsoft Agent Framework.

Only this adapter (and MAF-specific integration tests) may import MAF directly.

The adapter will translate stable `meuharness` contracts into MAF agents, workflows, middleware, tools, checkpoints and human-in-the-loop primitives.

Do not place ACTIS, coding or research logic here.
