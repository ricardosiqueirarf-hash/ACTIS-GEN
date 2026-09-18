from meuharness.core.errors import HarnessError
from meuharness import model_health


def test_model_health_classifies_limits():
    assert model_health._classify_error(
        HarnessError("request_rejected", "x", status_code=402)
    ) == "quota_exhausted"
    assert model_health._classify_error(
        HarnessError("rate_limited", "x", status_code=429)
    ) == "rate_limited"
    assert model_health._classify_error(
        HarnessError("gateway_error", "x", status_code=503)
    ) == "failing"


def test_model_health_snapshot_aggregates_catalog_routes(monkeypatch):
    scanner = model_health.ModelHealthScanner()
    monkeypatch.setattr(
        scanner,
        "refresh_catalog",
        lambda force=False: (
            "kc/nex-agi/nex-n2.5-pro:free",
            "kc/nvidia/nemotron-3-ultra-550b-a55b:free",
            "cx/gpt-6-astra",
        ),
    )
    monkeypatch.setattr(
        model_health,
        "_records_by_model",
        lambda: {
            "kc/nex-agi/nex-n2.5-pro:free": {
                "id": "kc/nex-agi/nex-n2.5-pro:free",
                "model": "kc/nex-agi/nex-n2.5-pro:free",
                "route": "kc",
                "provider": "Kilo Code",
                "status": "working",
                "last_checked_at": "2026-09-18T00:00:00Z",
                "next_check_at": "2026-09-18T06:00:00Z",
                "latency_ms": 900,
                "error": None,
                "error_code": None,
                "status_code": None,
            },
            "cx/gpt-6-astra": {
                "id": "cx/gpt-6-astra",
                "model": "cx/gpt-6-astra",
                "route": "cx",
                "provider": "Codex",
                "status": "rate_limited",
                "last_checked_at": "2026-09-18T00:00:00Z",
                "next_check_at": "2026-09-18T01:00:00Z",
                "latency_ms": 120,
                "error": "Gateway or provider rate limit reached.",
                "error_code": "rate_limited",
                "status_code": 429,
            },
        },
    )

    payload = scanner.snapshot()
    by_model = {row["model"]: row for row in payload["models"]}
    by_route = {row["route"]: row for row in payload["providers"]}

    assert by_model["kc/nex-agi/nex-n2.5-pro:free"]["status"] == "working"
    assert by_model["kc/nvidia/nemotron-3-ultra-550b-a55b:free"]["status"] == "unknown"
    assert by_route["kc"]["models"] == 2
    assert by_route["kc"]["working"] == 1
    assert by_route["kc"]["unknown"] == 1
    assert by_route["cx"]["rate_limited"] == 1


def test_provider_batch_is_bounded_and_catalog_scoped(monkeypatch):
    scanner = model_health.ModelHealthScanner()
    monkeypatch.setattr(
        scanner,
        "refresh_catalog",
        lambda force=False: tuple(f"kc/model-{i}" for i in range(20)) + ("cx/model-x",),
    )
    monkeypatch.setattr(model_health, "_records_by_model", lambda: {})

    queued = []
    monkeypatch.setattr(
        scanner,
        "enqueue",
        lambda model, source="manual_queue": queued.append((model, source)) or True,
    )

    result = scanner.enqueue_provider("kc", limit=5)
    assert len(result) == 5
    assert all(model.startswith("kc/") for model in result)
    assert all(source == "provider_manual" for _, source in queued)


def test_enriched_catalog_merges_kilo_free_custom_and_provider_models(monkeypatch):
    scanner = model_health.ModelHealthScanner()
    monkeypatch.setattr(scanner, "_public_catalog", lambda: ["cx/gpt-public"])

    def fake_admin(path, **kwargs):
        if path == "/api/models/custom":
            return {"models": [{"id": "big-pickle", "providerAlias": "oc", "type": "llm"}]}
        if path == "/api/providers/kilo/free-models":
            return {"models": [{"id": "nex-agi/nex-n2.5-pro:free"}]}
        if path == "/api/providers":
            return {"connections": [{"id": "codex-1", "provider": "codex", "isActive": True}]}
        if path == "/api/providers/codex-1/models":
            return {"models": [{"id": "gpt-reserve"}]}
        raise AssertionError(path)

    monkeypatch.setattr(model_health, "_admin_json", fake_admin)
    catalog = scanner.refresh_catalog(force=True)
    assert "cx/gpt-public" in catalog
    assert "cx/gpt-reserve" in catalog
    assert "kc/nex-agi/nex-n2.5-pro:free" in catalog
    assert "oc/big-pickle" in catalog


def test_native_probe_classifies_429_and_402_without_exposing_raw_error(monkeypatch):
    scanner = model_health.ModelHealthScanner()
    monkeypatch.setattr(scanner, "refresh_catalog", lambda force=False: ("cx/a", "kr/b"))

    monkeypatch.setattr(
        model_health,
        "_admin_json",
        lambda *args, **kwargs: {
            "ok": False,
            "latencyMs": 11,
            "status": 503,
            "error": "HTTP 503: [codex/a] [429]: The usage limit has been reached (reset after 4m 45s)",
        },
    )
    limited = scanner.probe_now("cx/a")
    assert limited["status"] == "rate_limited"
    assert "4m 45s" in limited["error"]
    assert "HTTP 503" not in limited["error"]

    monkeypatch.setattr(
        model_health,
        "_admin_json",
        lambda *args, **kwargs: {
            "ok": False,
            "latencyMs": 12,
            "status": 503,
            "error": 'HTTP 503: [kiro/b] [402]: {"reason":"MONTHLY_REQUEST_COUNT"}',
        },
    )
    quota = scanner.probe_now("kr/b")
    assert quota["status"] == "quota_exhausted"
    assert quota["error"] == "Quota mensal esgotada"


def test_scheduler_round_robins_routes(monkeypatch):
    scanner = model_health.ModelHealthScanner()
    monkeypatch.setattr(
        scanner,
        "refresh_catalog",
        lambda force=False: ("cl/a", "cl/b", "kc/free:free", "cx/c"),
    )
    monkeypatch.setattr(model_health, "_records_by_model", lambda: {})
    picks = [scanner._next_automatic() for _ in range(3)]
    assert {model_health.route_for_model(item) for item in picks} == {"cl", "kc", "cx"}
