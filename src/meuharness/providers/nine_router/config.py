from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from meuharness.core.errors import HarnessError


@dataclass(frozen=True)
class NineRouterSettings:
    api_key: str = field(repr=False)
    model: str
    base_url: str = "http://127.0.0.1:20128/v1"
    timeout_s: float = 30.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise HarnessError(
                "configuration", "Set NINEROUTER_API_KEY from the 9Router dashboard."
            )
        if not isinstance(self.model, str):
            raise HarnessError("configuration", "NINEROUTER_MODEL must be a string.")
        try:
            if not isinstance(self.base_url, str) or any(c.isspace() for c in self.base_url):
                raise ValueError("Invalid URL")
            url = urlsplit(self.base_url)
            _ = url.port
            valid = (
                url.scheme in ("http", "https")
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path.rstrip("/").endswith("/v1")
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise HarnessError(
                "configuration",
                "Use an HTTP(S) base URL ending in /v1, without credentials or query.",
            )
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (float, int))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise HarnessError(
                "configuration", "NINEROUTER_TIMEOUT_SECONDS must be finite and positive."
            )
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 5:
            raise HarnessError(
                "configuration", "NINEROUTER_MAX_RETRIES must be an integer between 0 and 5."
            )
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "api_key", self.api_key.strip())
        object.__setattr__(self, "model", self.model.strip())

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> NineRouterSettings:
        """Read an explicit file (default: cwd/.env); process environment wins."""
        path = Path(env_file) if env_file is not None else Path.cwd() / ".env"
        if env_file is not None and not path.is_file():
            raise HarnessError("configuration", "The explicitly selected env file does not exist.")
        values = {**(dotenv_values(path) if path.is_file() else {}), **os.environ}
        if model is not None:
            values["NINEROUTER_MODEL"] = model
        if timeout_s is not None:
            values["NINEROUTER_TIMEOUT_SECONDS"] = str(timeout_s)
        try:
            timeout = float(values.get("NINEROUTER_TIMEOUT_SECONDS") or "30")
        except ValueError:
            raise HarnessError(
                "configuration", "NINEROUTER_TIMEOUT_SECONDS must be a number."
            ) from None
        try:
            max_retries = int(values.get("NINEROUTER_MAX_RETRIES") or "1")
        except ValueError:
            raise HarnessError(
                "configuration", "NINEROUTER_MAX_RETRIES must be an integer between 0 and 5."
            ) from None
        return cls(
            api_key=values.get("NINEROUTER_API_KEY") or "",
            model=values.get("NINEROUTER_MODEL") or "",
            base_url=values.get("NINEROUTER_BASE_URL") or "http://127.0.0.1:20128/v1",
            timeout_s=timeout,
            max_retries=max_retries,
        )
