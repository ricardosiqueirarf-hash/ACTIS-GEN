"""Persistent, low-cost model health observation for the local 9Router gateway."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from meuharness.core.errors import HarnessError
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings
from meuharness.storage import load_collection, mutate_collection

_COLLECTION = "model_health"
_ROUTE_NAMES = {
    "cx": "Codex",
    "cl": "Cline / OpenRouter",
    "gh": "GitHub",
    "gc": "Gemini CLI",
    "kimi": "Kimi",
    "kr": "Kiro",
    "kc": "Kilo Code",
    "cbai": "CodeBuddy",
    "oc": "OpenCode Free",
    "opencode": "OpenCode Free",
    "combo": "Combo",
}
_PROVIDER_ROUTES = {
    "codex": "cx",
    "cline": "cl",
    "github": "gh",
    "gemini-cli": "gc",
    "kimi": "kimi",
    "kiro": "kr",
    "kilocode": "kc",
    "codebuddy-intl": "cbai",
    "opencode": "oc",
}
_STATUS_VALUES = {"working", "failing", "rate_limited", "quota_exhausted", "unknown"}
_ROUTER_BASE = "http://127.0.0.1:20128"
_ROUTER_DATA = Path.home() / ".9router"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def route_for_model(model: str) -> str:
    value = str(model or "").strip()
    if "/" not in value:
        return "combo"
    return value.split("/", 1)[0] or "other"


def provider_name(route: str) -> str:
    return _ROUTE_NAMES.get(route, route.upper())


def _probe_timeout() -> float:
    try:
        return max(3.0, min(float(os.getenv("ACTIS_MODEL_HEALTH_TIMEOUT_SECONDS", "18")), 60.0))
    except ValueError:
        return 18.0


def _probe_spacing() -> float:
    try:
        return max(2.0, min(float(os.getenv("ACTIS_MODEL_HEALTH_SPACING_SECONDS", "20")), 300.0))
    except ValueError:
        return 20.0


def _reset_seconds(raw: str | None) -> int | None:
    text = str(raw or "")
    match = re.search(
        r"reset\s+after\s+(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?",
        text,
        re.I,
    )
    if not match:
        return None
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total or None


def _cooldown_seconds(status: str, raw_error: str | None = None) -> int:
    reset = _reset_seconds(raw_error)
    if status == "rate_limited" and reset:
        return max(60, min(reset + 30, 6 * 3600))
    env_key = {
        "working": "ACTIS_MODEL_HEALTH_WORKING_SECONDS",
        "failing": "ACTIS_MODEL_HEALTH_FAILING_SECONDS",
        "rate_limited": "ACTIS_MODEL_HEALTH_RATE_LIMIT_SECONDS",
        "quota_exhausted": "ACTIS_MODEL_HEALTH_QUOTA_SECONDS",
    }.get(status)
    defaults = {
        "working": 6 * 3600,
        "failing": 3 * 3600,
        "rate_limited": 60 * 60,
        "quota_exhausted": 12 * 3600,
    }
    default = defaults.get(status, 30 * 60)
    if not env_key:
        return default
    try:
        return max(60, int(os.getenv(env_key, str(default))))
    except ValueError:
        return default


def _classify_error(exc: HarnessError) -> str:
    if exc.status_code == 402:
        return "quota_exhausted"
    if exc.status_code == 429 or exc.code == "rate_limited":
        return "rate_limited"
    return "failing"


def _classify_native_error(raw: str | None) -> str:
    text = str(raw or "").lower()
    if "[429]" in text or "rate limit" in text or "usage limit has been reached" in text:
        return "rate_limited"
    if (
        "[402]" in text
        or "monthly_request_count" in text
        or "insufficient balance" in text
        or "credits required" in text
        or "reached the limit" in text
    ):
        return "quota_exhausted"
    return "failing"


def _safe_native_error(status: str, raw: str | None) -> str | None:
    text = str(raw or "")
    reset = re.search(
        r"reset\s+after\s+(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?",
        text,
        re.I,
    )
    retry = ""
    if reset:
        bits = []
        if reset.group(1):
            bits.append(f"{reset.group(1)}h")
        if reset.group(2):
            bits.append(f"{reset.group(2)}m")
        if reset.group(3):
            bits.append(f"{reset.group(3)}s")
        if bits:
            retry = " · retry " + " ".join(bits)
    if status == "rate_limited":
        return "Rate limit do provider" + retry
    if status == "quota_exhausted":
        if "monthly_request_count" in text.lower():
            return "Quota mensal esgotada"
        return "Créditos/quota do provider indisponíveis"
    if not text:
        return None
    if "not supported" in text.lower():
        return "Modelo não suportado pela conta/provider"
    if "authentication" in text.lower() or "[401]" in text or "[403]" in text:
        return "Autenticação recusada pelo provider"
    return "Teste nativo do 9Router falhou"


def _records_by_model() -> dict[str, dict[str, Any]]:
    return {
        str(item.get("model") or item.get("id") or ""): item
        for item in load_collection(_COLLECTION)
        if str(item.get("model") or item.get("id") or "")
    }


def _save_record(record: dict[str, Any]) -> dict[str, Any]:
    model = str(record["model"])

    def apply(items: list[dict[str, Any]]) -> dict[str, Any]:
        for index, item in enumerate(items):
            if str(item.get("model") or item.get("id") or "") == model:
                items[index] = record
                return record
        items.append(record)
        return record

    return mutate_collection(_COLLECTION, apply)


def _cli_token() -> str:
    machine = (_ROUTER_DATA / "machine-id").read_text(encoding="utf-8").strip()
    secret = (_ROUTER_DATA / "auth" / "cli-secret").read_text(encoding="utf-8").strip()
    if not machine or not secret:
        raise RuntimeError("9Router local CLI authentication is unavailable")
    return hashlib.sha256((machine + "9r-cli-auth" + secret).encode()).hexdigest()[:16]


def _admin_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    headers = {
        "x-9r-cli-token": _cli_token(),
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _ROUTER_BASE + path,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    return value if isinstance(value, dict) else {}


def _relative_model_ids(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else []
    result: list[str] = []
    for item in rows:
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("model") or item.get("name")
        else:
            model_id = None
        if isinstance(model_id, str) and model_id.strip():
            result.append(model_id.strip())
    return result


class ModelHealthScanner:
    def __init__(self, env_file: str | None = None) -> None:
        self.env_file = env_file
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.RLock()
        self._catalog: tuple[str, ...] = ()
        self._catalog_at = 0.0
        self._last_probe_at: str | None = None
        self._last_catalog_error: str | None = None
        self._route_cursor = 0

    def start(self) -> "ModelHealthScanner":
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="actis-model-health", daemon=True
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _settings(self, *, model: str = "", timeout_s: float | None = None) -> NineRouterSettings:
        return NineRouterSettings.from_env(
            self.env_file,
            model=model,
            timeout_s=timeout_s,
        )

    def _public_catalog(self) -> list[str]:
        settings = self._settings(model="", timeout_s=10)
        return list(asyncio.run(NineRouterGateway(settings).list_models()))

    def _enriched_admin_models(self) -> list[str]:
        found: set[str] = set()

        try:
            payload = _admin_json("/api/models/custom", timeout=5)
            custom = payload.get("models") or payload.get("data") or []
            for item in custom if isinstance(custom, list) else []:
                if not isinstance(item, dict):
                    continue
                alias = str(item.get("providerAlias") or "").strip()
                model_id = str(item.get("id") or "").strip()
                kind = str(item.get("kind") or item.get("type") or "llm").lower()
                if alias and model_id and kind == "llm":
                    found.add(f"{alias}/{model_id}")
        except Exception:
            pass

        try:
            payload = _admin_json("/api/providers/kilo/free-models", timeout=8)
            for model_id in _relative_model_ids(payload.get("models") or payload.get("data")):
                found.add("kc/" + model_id.removeprefix("kc/"))
        except Exception:
            pass

        try:
            providers = _admin_json("/api/providers", timeout=5).get("connections") or []
        except Exception:
            providers = []

        for connection in providers if isinstance(providers, list) else []:
            if not isinstance(connection, dict) or connection.get("isActive") is False:
                continue
            provider = str(connection.get("provider") or "")
            route = _PROVIDER_ROUTES.get(provider)
            connection_id = str(connection.get("id") or "")
            if not route or not connection_id or provider == "kilocode":
                continue
            try:
                payload = _admin_json(f"/api/providers/{connection_id}/models", timeout=8)
            except Exception:
                continue
            for model_id in _relative_model_ids(payload.get("models") or payload.get("data")):
                if model_id.startswith(route + "/"):
                    found.add(model_id)
                else:
                    found.add(f"{route}/{model_id}")

        return sorted(found)

    def refresh_catalog(self, *, force: bool = False) -> tuple[str, ...]:
        with self._lock:
            fresh = self._catalog and not force and (time.monotonic() - self._catalog_at) < 900
            if fresh:
                return self._catalog

        found: set[str] = set()
        error: str | None = None
        try:
            found.update(self._public_catalog())
        except Exception as exc:
            error = str(exc)

        try:
            found.update(self._enriched_admin_models())
        except Exception:
            pass

        catalog = tuple(sorted(found, key=lambda value: (route_for_model(value), value)))
        with self._lock:
            if catalog:
                self._catalog = catalog
                self._catalog_at = time.monotonic()
                self._last_catalog_error = error
            elif error:
                self._last_catalog_error = error
            return self._catalog

    def _default_record(self, model: str) -> dict[str, Any]:
        route = route_for_model(model)
        return {
            "id": model,
            "model": model,
            "route": route,
            "provider": provider_name(route),
            "status": "unknown",
            "source": "catalog",
            "last_checked_at": None,
            "next_check_at": None,
            "latency_ms": None,
            "error": None,
            "error_code": None,
            "status_code": None,
        }

    def snapshot(self) -> dict[str, Any]:
        catalog = self.refresh_catalog()
        stored = _records_by_model()
        rows: list[dict[str, Any]] = []
        now = _utcnow()
        for model in catalog:
            row = self._default_record(model)
            row.update(stored.get(model) or {})
            checked = _parse(row.get("last_checked_at"))
            row["age_seconds"] = (
                max(0, int((now - checked).total_seconds())) if checked else None
            )
            if row.get("status") not in _STATUS_VALUES:
                row["status"] = "unknown"
            rows.append(row)

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            route = str(row.get("route") or route_for_model(row["model"]))
            group = grouped.setdefault(
                route,
                {
                    "route": route,
                    "provider": provider_name(route),
                    "models": 0,
                    "working": 0,
                    "failing": 0,
                    "rate_limited": 0,
                    "quota_exhausted": 0,
                    "unknown": 0,
                    "last_checked_at": None,
                    "last_latency_ms": None,
                },
            )
            group["models"] += 1
            status = str(row.get("status") or "unknown")
            group[status] = int(group.get(status, 0)) + 1
            checked = row.get("last_checked_at")
            if checked and (not group["last_checked_at"] or checked > group["last_checked_at"]):
                group["last_checked_at"] = checked
                group["last_latency_ms"] = row.get("latency_ms")

        with self._lock:
            scanner = {
                "running": bool(self._thread and self._thread.is_alive()),
                "queued": self._queue.qsize(),
                "last_probe_at": self._last_probe_at,
                "catalog_error": self._last_catalog_error,
                "spacing_seconds": _probe_spacing(),
                "catalog_size": len(catalog),
            }
        return {"models": rows, "providers": list(grouped.values()), "scanner": scanner}

    def _due(self, model: str, record: dict[str, Any] | None) -> bool:
        if "/" not in model:
            return False
        if not record or not record.get("last_checked_at"):
            return True
        next_check = _parse(record.get("next_check_at"))
        return next_check is None or next_check <= _utcnow()

    def enqueue(self, model: str, *, source: str = "manual_queue") -> bool:
        model = str(model or "").strip()
        if not model:
            return False
        catalog = self.refresh_catalog()
        if catalog and model not in catalog:
            return False
        with self._lock:
            if model in self._queued:
                return False
            self._queued.add(model)
            self._queue.put((model, source))
            self._wake.set()
        return True

    def enqueue_provider(self, route: str, *, limit: int = 12) -> list[str]:
        route = str(route or "").strip()
        candidates = [m for m in self.refresh_catalog() if route_for_model(m) == route]
        stored = _records_by_model()
        candidates.sort(
            key=lambda m: (
                0 if (":free" in m.lower() or "/free" in m.lower()) else 1,
                0 if not stored.get(m, {}).get("last_checked_at") else 1,
                str(stored.get(m, {}).get("last_checked_at") or ""),
            )
        )
        queued: list[str] = []
        for model in candidates:
            if len(queued) >= max(1, min(int(limit), 50)):
                break
            if self.enqueue(model, source="provider_manual"):
                queued.append(model)
        return queued

    def probe_now(self, model: str, *, source: str = "manual") -> dict[str, Any]:
        model = str(model or "").strip()
        catalog = self.refresh_catalog()
        if catalog and model not in catalog:
            raise HarnessError("not_found", "Model is not present in the observed 9Router catalog.", status_code=404)
        return self._probe(model, source=source)

    def _native_probe(self, model: str) -> tuple[str, int | None, str | None, int]:
        started = time.perf_counter()
        payload = _admin_json(
            "/api/models/test",
            method="POST",
            payload={"model": model},
            timeout=_probe_timeout(),
        )
        latency = payload.get("latencyMs")
        if not isinstance(latency, (int, float)):
            latency = round((time.perf_counter() - started) * 1000)
        raw_error = str(payload.get("error") or "")
        if payload.get("ok") is True:
            return "working", int(latency), None, int(payload.get("status") or 200)
        status = _classify_native_error(raw_error)
        return status, int(latency), raw_error, int(payload.get("status") or 0)

    def _gateway_probe(self, model: str) -> tuple[str, int | None, str | None, int | None]:
        started = time.perf_counter()
        try:
            settings = self._settings(model=model, timeout_s=_probe_timeout())
            asyncio.run(
                NineRouterGateway(settings).complete(
                    "Reply only OK.",
                    max_output_tokens=8,
                )
            )
            return "working", round((time.perf_counter() - started) * 1000), None, 200
        except HarnessError as exc:
            return (
                _classify_error(exc),
                round((time.perf_counter() - started) * 1000),
                str(exc),
                exc.status_code,
            )

    def _probe(self, model: str, *, source: str) -> dict[str, Any]:
        checked_at = _utcnow()
        raw_error: str | None = None
        try:
            status, latency_ms, raw_error, status_code = self._native_probe(model)
            probe_source = source + ":9router_native"
        except Exception:
            try:
                status, latency_ms, raw_error, status_code = self._gateway_probe(model)
                probe_source = source + ":gateway_fallback"
            except Exception:
                status = "failing"
                latency_ms = None
                status_code = None
                raw_error = "Unexpected model probe failure."
                probe_source = source + ":probe_error"

        next_check = checked_at + timedelta(seconds=_cooldown_seconds(status, raw_error))
        record = {
            "id": model,
            "model": model,
            "route": route_for_model(model),
            "provider": provider_name(route_for_model(model)),
            "status": status,
            "source": probe_source,
            "last_checked_at": _iso(checked_at),
            "next_check_at": _iso(next_check),
            "latency_ms": latency_ms,
            "error": _safe_native_error(status, raw_error),
            "error_code": status if status != "working" else None,
            "status_code": status_code,
        }
        _save_record(record)
        with self._lock:
            self._last_probe_at = record["last_checked_at"]
        return record

    def _next_automatic(self) -> str | None:
        catalog = self.refresh_catalog()
        stored = _records_by_model()
        due_by_route: dict[str, list[str]] = {}
        for model in catalog:
            if self._due(model, stored.get(model)):
                with self._lock:
                    if model in self._queued:
                        continue
                due_by_route.setdefault(route_for_model(model), []).append(model)

        routes = sorted(route for route, values in due_by_route.items() if values and route != "combo")
        if not routes:
            return None

        with self._lock:
            index = self._route_cursor % len(routes)
            route = routes[index]
            self._route_cursor = (index + 1) % len(routes)

        candidates = due_by_route[route]
        candidates.sort(
            key=lambda m: (
                0 if (":free" in m.lower() or "/free" in m.lower()) else 1,
                0 if not stored.get(m, {}).get("last_checked_at") else 1,
                str(stored.get(m, {}).get("last_checked_at") or ""),
            )
        )
        return candidates[0] if candidates else None

    def _run(self) -> None:
        while not self._stop.is_set():
            model: str | None = None
            source = "scanner"
            try:
                model, source = self._queue.get_nowait()
                with self._lock:
                    self._queued.discard(model)
            except queue.Empty:
                model = self._next_automatic()

            if not model:
                self._wake.wait(30)
                self._wake.clear()
                continue

            try:
                self._probe(model, source=source)
            except Exception:
                pass

            if self._stop.wait(_probe_spacing()):
                break


_SCANNER_LOCK = threading.Lock()
_SCANNER: ModelHealthScanner | None = None


def get_model_health_scanner(env_file: str | None = None) -> ModelHealthScanner:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is None:
            _SCANNER = ModelHealthScanner(env_file)
        return _SCANNER.start()
