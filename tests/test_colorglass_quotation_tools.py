from meuharness.colorglass_quotation_tools import build_colorglass_quotation_tools
from meuharness.skills import instructions_for_skills, runtime_features_for_skills

def test_colorglass_quotation_skill_enables_native_runtime():
    features = runtime_features_for_skills(["colorglass.quotation"])
    assert "browser.native_transport" in features
    assert "colorglass.quotation.native" in features
    assert "colorglass_quote_add_door" in instructions_for_skills(["colorglass.quotation"])

def test_colorglass_native_tool_catalog_is_exposed():
    names = {tool.__name__ for tool in build_colorglass_quotation_tools("quote-agent")}
    assert names == {
        "colorglass_quote_create",
        "colorglass_quote_add_door",
        "colorglass_quote_patch_existing",
        "colorglass_quote_verify",
        "colorglass_quote_pdf",
        "colorglass_list_orders",
    }

def test_colorglass_skill_instructs_auth_required_detection():
    instructions = instructions_for_skills(["colorglass.quotation"])
    assert "auth_required" in instructions
    assert "ok:false" in instructions
    assert "order_not_found" in instructions
    assert "colorglass_list_orders" in instructions
    assert "colorglass_quote_patch_existing" in instructions
    assert "colorglass_session_info" in instructions
