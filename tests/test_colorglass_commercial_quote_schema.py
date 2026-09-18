import json

from meuharness.colorglass_fast_tools import QUOTE_SCHEMAS, build_colorglass_fast_tools


def test_quote_schemas_have_only_minimum_required_customer_fields():
    expected = {"quantity", "width_mm", "height_mm", "profile_code", "color", "glass"}
    assert set(QUOTE_SCHEMAS) == {"giro", "fixa", "deslizante"}
    for schema in QUOTE_SCHEMAS.values():
        assert set(schema["required"]) == expected
        assert schema["compose"]["profile"] == "<profile_code> <color>"


def test_sliding_system_fields_are_optional_not_customer_form_fields():
    schema = QUOTE_SCHEMAS["deslizante"]
    assert "system" not in schema["required"]
    assert "upper_track" not in schema["required"]
    assert "lower_track" not in schema["required"]
    assert {"system", "upper_track", "lower_track"} <= set(schema["optional"])
    assert {"system", "upper_track", "lower_track"} <= set(schema["price_sensitive"])


def test_schema_exposes_different_commercial_and_technical_contracts():
    tools = {tool.__name__: tool for tool in build_colorglass_fast_tools("wa")}
    commercial = json.loads(tools["colorglass_quote_schema"]("giro", "commercial"))
    technical = json.loads(tools["colorglass_quote_schema"]("giro", "technical"))
    assert commercial["phase"] == "commercial"
    assert "hinge_heights_mm" not in commercial["schema"]["required"]
    assert technical["phase"] == "technical"
    assert "hinge_heights_mm" in technical["schema"]["required"]
    assert "drilling_position" in technical["schema"]["required"]
