"""Explicit application composition; core remains unaware of runtime/provider choices."""

from meuharness import Harness
from meuharness.adapters.maf import MAFRuntime
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings


def create_harness(settings: NineRouterSettings | None = None) -> Harness:
    return Harness(MAFRuntime(NineRouterGateway(settings or NineRouterSettings.from_env())))
