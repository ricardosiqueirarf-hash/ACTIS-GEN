from meuharness.colorglass_fast_tools import QUOTE_SCHEMAS


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
