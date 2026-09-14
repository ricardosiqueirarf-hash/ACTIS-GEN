"""Bounded, progressive connectivity diagnosis, retaining the original CLI name."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
import time
from dataclasses import asdict
from uuid import uuid4

from meuharness import HarnessError, HarnessRequest
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings


def emit(stage: str, status: str, **fields: object) -> None:
    print(json.dumps({"stage": stage, "status": status, **fields}, ensure_ascii=False), flush=True)


async def diagnose(args: argparse.Namespace) -> int:
    stage = "configuration"
    try:
        # --list-models works before a default model has been selected.
        settings = NineRouterSettings.from_env(
            args.env_file,
            model=args.model,
            timeout_s=args.timeout,
        )
        emit(
            stage,
            "ok",
            base_url=settings.base_url,
            model=settings.model,
            api_key="configured",
            timeout_s=settings.timeout_s,
        )
        gateway = NineRouterGateway(settings)
        stage = "models"
        emit(stage, "started")
        models = await gateway.list_models()
        emit(stage, "ok", count=len(models), selected_model_present=settings.model in models)
        if args.list_models:
            emit("catalog", "ok", models=models)
            return 0
        if settings.model not in models:
            raise HarnessError(
                "model_not_listed", "Selected model is absent from /v1/models; use --list-models."
            )
        expected = "meuharness-probe-" + uuid4().hex[:10]
        prompt = "Responda somente com este texto, sem aspas: " + expected
        stage = "direct_completion"
        emit(stage, "started")
        start = time.monotonic()
        direct = await gateway.complete(prompt)
        if direct.text.strip() != expected:
            raise HarnessError("verification", "Direct reply did not match the connectivity probe.")
        emit(
            stage,
            "ok",
            elapsed_s=round(time.monotonic() - start, 2),
            text=direct.text,
            probe_match=True,
        )
        stage = "maf_completion"
        emit(stage, "started")
        from meuharness.bootstrap import create_harness

        result = await create_harness(settings).run(
            HarnessRequest(
                prompt=prompt,
                instructions="Connectivity test. Return the requested text only.",
                timeout_s=settings.timeout_s,
                max_output_tokens=100,
            )
        )
        if result.text.strip() != expected:
            raise HarnessError("verification", "MAF reply did not match the connectivity probe.")
        emit(stage, "ok", **asdict(result), probe_match=True)
        versions = {}
        for package in ("agent-framework-core", "agent-framework-openai", "openai"):
            versions[package] = importlib.metadata.version(package)
        emit("complete", "ok", message="MAF -> 9Router funcionando.", versions=versions)
        return 0
    except HarnessError as exc:
        emit(
            stage,
            "error",
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
            http_status=exc.status_code,
        )
        return 1
    except Exception:  # noqa: BLE001 - CLI must never print raw provider errors
        emit(
            stage,
            "error",
            code="diagnostic_error",
            message="Unexpected diagnostic failure; check installed dependencies.",
        )
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Explicit .env path; defaults to current directory/.env")
    parser.add_argument("--model", help="Explicit model ID from /v1/models; does not edit .env")
    parser.add_argument("--timeout", type=float, help="Maximum seconds per diagnostic stage")
    parser.add_argument(
        "--list-models", action="store_true", help="List catalog without invoking a model"
    )
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(diagnose(args)))
    except KeyboardInterrupt:
        emit("cancelled", "error", message="Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
