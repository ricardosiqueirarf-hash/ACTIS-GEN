from pathlib import Path

from meuharness.skills import (
    active_skill_ids,
    instructions_for_skills,
    progressive_skill_context,
    runtime_features_for_skills,
    scopes_for_skills,
    tools_for_skills,
)


def test_desktop_skill_inherits_all_operator_tools():
    tools = set(tools_for_skills(["desktop.operator"]))
    assert {"harness_computer", "harness_browser", "harness_files", "harness_terminal"} <= tools


def test_whatsapp_customer_service_inherits_transport_and_local_runtime():
    assert "harness_browser" in tools_for_skills(["whatsapp.customer-service"])
    assert {"browser.read", "browser.interact"} <= set(scopes_for_skills(["whatsapp.customer-service"]))
    assert "whatsapp.local" in runtime_features_for_skills(["whatsapp.customer-service"])
    assert "WHATSAPP TRANSPORT" in instructions_for_skills(["browser.whatsapp"])


def test_whatsapp_skills_use_progressive_disclosure():
    active = active_skill_ids(["whatsapp.customer-service"], "mande uma mensagem para Ricardo")
    assert "browser.whatsapp" in active
    assert "whatsapp.messaging" in active
    assert "whatsapp.routing" not in active
    context = progressive_skill_context(["whatsapp.customer-service"], "mande uma mensagem para Ricardo")
    assert "SKILLS DISPONÍVEIS" in context
    assert "WHATSAPP MESSAGING" in context
    assert "WHATSAPP ROUTING" not in context


def test_execution_kernel_has_no_agent_id_domain_switches():
    source = Path("src/meuharness/execution_service.py").read_text()
    assert '"whatsapp" in agent_id.lower()' not in source
    assert 'agent.get("id") == "general"' not in source
    assert 'agent_id == "general"' not in source

def test_whatsapp_standard_pack_resolves_complete_default_stack():
    skill_id = "whatsapp.standard-pack"
    resolved = set(__import__("meuharness.skills", fromlist=["_resolved_ids"])._resolved_ids([skill_id]))
    assert {
        "browser.whatsapp", "whatsapp.messaging", "whatsapp.inbox", "whatsapp.labels",
        "whatsapp.routing", "whatsapp.ai-agent", "whatsapp.customer-service", "writing.humanizer", skill_id,
    } <= resolved
    assert "harness_browser" in tools_for_skills([skill_id])
    assert "whatsapp.local" in runtime_features_for_skills([skill_id])
    assert "WHATSAPP STANDARD PACK" in instructions_for_skills([skill_id])



def test_pdf_standard_pack_is_inheritable_and_resolves_runtime():
    skill_id = "pdf.standard-pack"
    resolved = set(__import__("meuharness.skills", fromlist=["_resolved_ids"])._resolved_ids([skill_id]))
    assert {"pdf.operator", "pdf.create", "pdf.edit", skill_id} <= resolved
    assert "pdf_toolkit" in tools_for_skills([skill_id])
    assert {"pdf.read", "pdf.write"} <= set(scopes_for_skills([skill_id]))
    context = progressive_skill_context([skill_id], "crie um PDF com este relatório")
    assert "PDF OPERATOR" in context
    assert "PDF CREATE" in context


def test_humanizer_is_file_backed_and_progressively_loaded():
    idle = progressive_skill_context(["writing.humanizer"], "liste os arquivos desta pasta")
    assert "writing.humanizer" in idle
    assert "Humanizer: remove AI writing patterns" not in idle

    active = progressive_skill_context(
        ["writing.humanizer"],
        "responda esta mensagem de forma natural para o cliente",
    )
    assert "Humanizer: remove AI writing patterns" in active
    assert "ACTIS EMBEDDED MODE" in active

    skill_file = Path("src/meuharness/skill_assets/humanizer/SKILL.md")
    license_file = Path("src/meuharness/skill_assets/humanizer/LICENSE")
    assert skill_file.exists()
    assert license_file.exists()
    assert "MIT License" in license_file.read_text(encoding="utf-8")


def test_social_skills_can_inherit_humanizer_without_loading_it_for_read_only_tasks():
    resolved = __import__("meuharness.skills", fromlist=["_resolved_ids"])._resolved_ids(
        ["browser.instagram"]
    )
    assert "writing.humanizer" in resolved

    read_only = progressive_skill_context(["browser.instagram"], "abra o perfil no navegador")
    assert "Humanizer: remove AI writing patterns" not in read_only

    outbound = progressive_skill_context(
        ["browser.instagram"],
        "responda o comentário com uma mensagem curta",
    )
    assert "Humanizer: remove AI writing patterns" in outbound


def test_company_run_manager_skill_focuses_on_recovery_and_reporting_without_dev_escalation():
    context = progressive_skill_context(
        ["company.run-manager"],
        "a run falhou; recupere e gere o relatório",
    )
    assert "COMPANY RUN MANAGER" in context
    assert "company_create_run_report" in context
    assert "não acione DEV/OpenCode automaticamente" in context
