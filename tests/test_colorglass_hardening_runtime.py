from __future__ import annotations

import base64
import json


def _tool(name: str, *, run_id: str | None = None):
    from meuharness.colorglass_quotation_tools import build_colorglass_quotation_tools
    return next(t for t in build_colorglass_quotation_tools("quote-agent", run_id=run_id) if t.__name__ == name)


def test_colorglass_skill_is_native_first_not_browser_operator():
    from meuharness.skills import instructions_for_skills
    text = instructions_for_skills(["colorglass.quotation"])
    assert "colorglass_session_info" in text
    assert "colorglass_quote_patch_existing" in text
    assert "A primeira ação deve ser uma tool browser_*" not in text


def test_patch_existing_requires_verified_fingerprint_before_browser_use():
    result = json.loads(_tool("colorglass_quote_patch_existing")("513", "", glass="Zero"))
    assert result["ok"] is False
    assert result["status"] == "needs_verification"


def test_patch_existing_contains_conflict_gate_and_official_recalculation(monkeypatch):
    import meuharness.colorglass_quotation_tools as mod
    calls: list[str] = []
    def fake_eval(agent_id, expression, *, await_promise=False):
        calls.append(expression)
        if len(calls) == 1:
            return {"ok": True, "id": "uuid-513", "numero_pedido": 513, "cliente": "Livia"}
        if len(calls) == 2:
            return True
        return {"ok": False, "status": "conflict", "expected": "abc", "current": "def"}
    monkeypatch.setattr(mod, "_eval", fake_eval)
    monkeypatch.setattr(mod, "_active", lambda agent_id: (None, None, "ws"))
    monkeypatch.setattr(mod, "_cdp", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_wait_ready", lambda *a, **k: "ws")
    result = json.loads(_tool("colorglass_quote_patch_existing")("513", "abc", glass="Zero", additional_delta=80))
    assert result["status"] == "conflict"
    mutation_js = calls[-1]
    assert "calcularPrecoPorta" in mutation_js
    assert "salvarPortasBackend" in mutation_js
    assert "changed_before_write" in mutation_js
    assert "beforeFingerprint" in mutation_js


def test_pdf_export_registers_real_run_artifact(monkeypatch, tmp_path):
    import meuharness.colorglass_quotation_tools as mod
    monkeypatch.setattr(mod.storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "_eval", lambda *a, **k: {"ok": True, "id": "uuid-513", "numero_pedido": 513, "cliente": "Livia", "quantidade_total": 7, "valor_total": 7484.97})
    monkeypatch.setattr(mod, "_active", lambda agent_id: (None, None, "ws"))
    def fake_cdp(ws, method, params=None, timeout_s=None):
        if method == "Page.printToPDF":
            raw = b"%PDF-1.7\n" + (b"x" * 1800)
            return {"data": base64.b64encode(raw).decode("ascii")}
        return {}
    monkeypatch.setattr(mod, "_cdp", fake_cdp)
    monkeypatch.setattr(mod, "_value", lambda *a, **k: True)
    registered = []
    def fake_register(path, **kwargs):
        registered.append((path, kwargs))
        return {"id": "artifact-513"}
    monkeypatch.setattr(mod, "register_artifact", fake_register)
    result = mod.export_colorglass_quote_pdf("quote-agent", "513", run_id="run-513")
    assert result["ok"] is True
    assert result["artifact_id"] == "artifact-513"
    assert registered[0][1]["run_id"] == "run-513"
    assert registered[0][1]["mime_type"] == "application/pdf"
    assert registered[0][0].is_file()
