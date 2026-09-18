from meuharness.control_tools import build_general_tools
from meuharness.feature_registry import feature_ids, list_feature_catalog
from meuharness.skills import SKILL_CATALOG
from meuharness.tool_registry import list_tool_catalog


def test_feature_catalog_covers_current_product_surface():
    expected = {
        "agents", "organization", "context", "memory", "project-memory", "conversations", "runs-world",
        "automations", "workflows", "approvals", "tools-skills", "models",
        "connectors", "channels", "whatsapp", "harnesses", "desktop-assistant",
    }
    assert expected <= feature_ids()
    assert all(row["general_mode"] in {"direct", "hybrid", "delegate"} for row in list_feature_catalog())


def test_actis_admin_advertises_general_feature_surface():
    admin = next(row for row in list_tool_catalog() if row["id"] == "actis_admin")
    capabilities = set(admin["capabilities"])
    assert {"features", "memory", "project_memory", "company_workspace", "connector_admin", "channel_admin", "whatsapp_admin"} <= capabilities


def test_general_tools_cover_current_admin_surface():
    names = {tool.__name__ for tool in build_general_tools()}
    expected = {
        "actis_list_features", "actis_recall_agent_memory", "actis_delete_agent",
        "actis_bind_project_memory", "actis_project_memory_catalog", "actis_project_memory_recall",
        "actis_project_memory_write", "actis_project_memory_promote",
        "actis_save_connector", "actis_test_connector", "actis_delete_connector",
        "actis_create_channel", "actis_set_channel_members",
        "actis_update_channel_message", "actis_delete_channel_message", "actis_delete_channel",
        "actis_get_whatsapp_state", "actis_whatsapp_monitor_contact",
        "actis_whatsapp_sync", "actis_whatsapp_set_automation",
    }
    assert expected <= names


def test_control_plane_skill_uses_dynamic_feature_discovery():
    skill = SKILL_CATALOG["actis.control-plane"]
    assert "actis_list_features" in skill["instructions"]
    assert "actis.state" in skill["runtime_features"]
    assert "actis_admin" in skill["tools"]


def test_agents_feature_advertises_delete_action():
    agents = next(row for row in list_feature_catalog() if row["id"] == "agents")
    assert "delete" in agents["general_actions"]
