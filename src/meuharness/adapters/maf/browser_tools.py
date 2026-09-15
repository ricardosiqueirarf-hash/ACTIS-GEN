"""MAF-backed external tool factories."""

from __future__ import annotations

import os
from pathlib import Path

from agent_framework import MCPStdioTool

from meuharness.adapters.maf.observed_tool import ObservedMCPStdioTool
from meuharness.browser_manager import ensure_browser

BROWSER_READ_TOOLS = (
    "browser_page_info", "browser_screenshot", "browser_list_tabs",
    "browser_current_tab", "browser_wait", "browser_wait_for_load",
    "browser_wait_for_element", "browser_http_get", "browser_ensure_real_tab",
)
BROWSER_INTERACT_TOOLS = (
    "browser_new_tab", "browser_goto", "browser_click", "browser_type",
    "browser_fill", "browser_press", "browser_scroll", "browser_switch_tab",
    "browser_close_tab", "browser_js", "browser_cdp", "browser_upload_file",
    "browser_start_recording", "browser_stop_recording",
)


def build_harness_browser(scopes: set[str] | None = None, *, run_id: str | None = None, agent_id: str | None = None) -> MCPStdioTool:
    """Return Browser Harness restricted by browser.read / browser.interact scopes."""
    command = str(Path.home() / ".local" / "bin" / "browser-harness-mcp")
    runtime = ensure_browser(agent_id or "shared")
    env = dict(os.environ)
    env["BU_CDP_URL"] = runtime.cdp_url
    env["ACTIS_BROWSER_PROFILE"] = runtime.profile_dir
    env["ACTIS_BROWSER_AGENT_ID"] = runtime.agent_id
    allowed = None
    if scopes is not None:
        names: list[str] = []
        if "browser.read" in scopes:
            names.extend(BROWSER_READ_TOOLS)
        if "browser.interact" in scopes:
            names.extend(BROWSER_READ_TOOLS)
            names.extend(BROWSER_INTERACT_TOOLS)
        allowed = tuple(dict.fromkeys(names))
    return ObservedMCPStdioTool(
        name="harness_browser",
        actis_run_id=run_id, actis_agent_id=agent_id,
        command=command,
        env=env,
        load_prompts=False,
        request_timeout=30,
        description="Navegação web real via Browser Harness/CDP.",
        approval_mode="never_require",
        allowed_tools=allowed,
    )
