from __future__ import annotations

from meuharness.domains import whatsapp_quote_state as qs


def test_extracts_operator_quote_without_reasking_dimensions_or_hinges():
    fields = qs.extract_quote_fields(
        "Orça pra mim duas portas 1036 prata e esp. Prata. 800x300 so duas dobradiças."
    )
    assert fields == {
        "quantity": 2,
        "color": "prata",
        "profile_code": "1036",
        "profile": "1036 prata",
        "glass": "esp prata",
        "height_mm": 800,
        "width_mm": 300,
        "hinges": 2,
        "typology": "giro",
    }


def test_extracts_explicit_hinge_heights_separately_from_door_dimensions():
    fields = qs.extract_quote_fields(
        "800 de altura, 300 de largura. 100 e 700 é a altura da dobradiça."
    )
    assert fields["height_mm"] == 800
    assert fields["width_mm"] == 300
    assert fields["hinge_heights_mm"] == [100, 700]


def test_affirmative_consumes_pending_confirmation(monkeypatch):
    current = {
        "fields": {"height_mm": 800, "width_mm": 300},
        "status": "needs_information",
        "pending_confirmation": {"question": "Confirma 800x300?", "proposed": {"height_mm": 800, "width_mm": 300}},
        "revision": 2,
    }
    saved = {}
    monkeypatch.setattr(qs, "get_quote_state", lambda agent_id, contact: dict(current))
    monkeypatch.setattr(qs, "save_quote_state", lambda agent_id, contact, quote: saved.update(quote) or dict(quote))
    result = qs.update_quote_state("wa", "85988241620", "Isso")
    assert result["pending_confirmation"] is None
    assert result["fields"]["height_mm"] == 800
    assert result["fields"]["width_mm"] == 300
    assert result["status"] == "collecting"


def test_quote_detection_is_operational_not_greeting():
    assert qs.looks_like_quote("Orça duas portas 1036 prata") is True
    assert qs.looks_like_quote("Oi, tudo bem?") is False


def test_formats_specialist_result_as_short_operator_reply():
    text = """O orçamento foi criado e salvo com sucesso.\n- **Número do Pedido:** 509\n- **Quantidade Total:** 2 portas\n- **Valor Total:** R$ 444,13\n- **Perfil:** 1036 prata\n- **Vidro:** espelho prata (4mm)\n- **Altura:** 800 mm\n- **Largura:** 300 mm\n"""
    reply = qs.format_quote_reply(text)
    assert "pedido #509" in reply
    assert "2 portas" in reply
    assert "800×300 mm" in reply
    assert "R$ 444,13" in reply
    assert len(reply) < 220


def test_new_quote_request_does_not_inherit_previous_fields(monkeypatch):
    current = {"fields": {"glass": "esp prata", "height_mm": 800}, "status": "quoted", "pending_confirmation": None, "revision": 3}
    monkeypatch.setattr(qs, "get_quote_state", lambda agent_id, contact: dict(current))
    monkeypatch.setattr(qs, "save_quote_state", lambda agent_id, contact, quote: dict(quote))
    result = qs.update_quote_state("wa", "op", "Orça duas portas 070 bronze 1000x400")
    assert result["fields"]["profile"] == "070 bronze"
    assert result["fields"]["height_mm"] == 1000
    assert result["fields"]["width_mm"] == 400
    assert "glass" not in result["fields"]


def test_short_answer_fills_exact_pending_field(monkeypatch):
    current = {"fields": {"height_mm": 800}, "status": "needs_information", "pending_confirmation": {"question": "Qual lado das dobradiças?", "proposed": {}}, "revision": 4}
    monkeypatch.setattr(qs, "get_quote_state", lambda agent_id, contact: dict(current))
    monkeypatch.setattr(qs, "save_quote_state", lambda agent_id, contact, quote: dict(quote))
    result = qs.update_quote_state("wa", "op", "Esquerda")
    assert result["fields"]["hinge_side"] == "esquerda"
    assert result["pending_confirmation"] is None


def test_alteration_uses_existing_order_without_requiring_full_door(monkeypatch):
    current = {
        "fields": {"glass": "esp prata", "height_mm": 800, "width_mm": 300},
        "status": "quoted", "quote_mode": "new", "order_ref": "509",
        "pending_confirmation": None, "revision": 5,
    }
    monkeypatch.setattr(qs, "get_quote_state", lambda agent_id, contact: dict(current))
    monkeypatch.setattr(qs, "save_quote_state", lambda agent_id, contact, quote: dict(quote))
    result = qs.update_quote_state("wa", "op", "No pedido 509 troque o vidro para reflecta bronze")
    assert result["quote_mode"] == "alteration"
    assert result["order_ref"] == "509"
    assert result["fields"]["glass"] == "reflecta bronze"
    assert qs.quote_missing_fields(result) == []


def test_alteration_without_order_reference_asks_only_for_reference():
    quote = {"quote_mode": "alteration", "order_ref": "", "fields": {}}
    assert qs.quote_missing_fields(quote) == ["número do pedido/orçamento"]


def test_quote_payload_carries_mode_and_order_reference():
    quote = {"fields": {"glass": "esp prata"}, "quote_mode": "alteration", "order_ref": "510", "revision": 2}
    payload = qs.quote_runtime_payload(quote, contact_name="Cemari", latest_text="troque o vidro")
    assert payload["quote_mode"] == "alteration"
    assert payload["order_ref"] == "510"

def test_extracts_natural_color_and_colon_dimensions_without_reasking_them():
    fields = qs.extract_quote_fields(
        "2 portas de giro, bronze, espelho prata. Altura: 300, largura: 200."
    )
    assert fields["quantity"] == 2
    assert fields["typology"] == "giro"
    assert fields["color"] == "bronze"
    assert fields["glass"] == "esp prata"
    assert fields["height_mm"] == 300
    assert fields["width_mm"] == 200
    quote = {"quote_mode": "new", "fields": fields}
    assert qs.quote_missing_fields(quote) == ["perfil"]
