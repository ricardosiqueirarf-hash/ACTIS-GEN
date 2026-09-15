"""Ready-made MCP integrations for local ACTIS GEN tools."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_framework import MCPStdioTool
from agent_framework.exceptions import ToolExecutionException

from meuharness.adapters.maf.observed_tool import ObservedMCPStdioTool
from meuharness.providers.nine_router import NineRouterGateway, NineRouterSettings

NODE_BIN = Path.home() / ".nvm" / "versions" / "node" / "v24.21.0" / "bin"
HOME = str(Path.home())


def _binary(name: str) -> str:
    return str(NODE_BIN / name)


FILES_READ_TOOLS = (
    "read_file", "read_text_file", "read_media_file", "read_multiple_files",
    "list_directory", "list_directory_with_sizes", "directory_tree",
    "search_files", "get_file_info", "list_allowed_directories",
)
FILES_WRITE_TOOLS = ("write_file", "edit_file", "create_directory", "move_file")


def build_harness_files(scopes: set[str] | None = None, *, run_id: str | None = None, agent_id: str | None = None) -> MCPStdioTool:
    """Official MCP Filesystem server scoped to home and optional capability scopes."""
    allowed = None
    if scopes is not None:
        names: list[str] = []
        if "files.read" in scopes:
            names.extend(FILES_READ_TOOLS)
        if "files.write" in scopes:
            names.extend(FILES_WRITE_TOOLS)
        allowed = tuple(dict.fromkeys(names))
    return ObservedMCPStdioTool(
        name="harness_files",
        actis_run_id=run_id, actis_agent_id=agent_id,
        command=_binary("mcp-server-filesystem"),
        args=[HOME],
        load_prompts=False,
        request_timeout=30,
        approval_mode="never_require",
        allowed_tools=allowed,
    )


def build_harness_terminal(*, run_id: str | None = None, agent_id: str | None = None) -> MCPStdioTool:
    """MCP Shell Server for local terminal/process execution."""
    env = dict(os.environ)
    env["MCP_SHELL_SECURITY_MODE"] = "permissive"
    env["MCP_SHELL_ENHANCED_MODE"] = "false"
    env["MCP_SHELL_LLM_EVALUATION"] = "false"
    env["MCP_SHELL_DEFAULT_WORKDIR"] = HOME
    env["MCP_SHELL_ALLOWED_WORKDIRS"] = HOME
    return ObservedMCPStdioTool(
        name="harness_terminal",
        actis_run_id=run_id, actis_agent_id=agent_id,
        command=_binary("mcp-shell-server"),
        env=env,
        load_prompts=False,
        request_timeout=60,
        approval_mode="never_require",
    )


def _active_graphics_env() -> dict[str, str]:
    """Discover the user's live graphical session when ACTIS runs outside the desktop shell."""
    keys = ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XAUTHORITY")
    candidates: list[tuple[int, dict[str, str]]] = []
    proc_root = Path("/proc")
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
            if "Xwayland" not in cmdline and "Xorg" not in cmdline:
                continue
            values = {}
            for raw in (proc / "environ").read_bytes().split(b"\0"):
                if b"=" not in raw:
                    continue
                key, value = raw.split(b"=", 1)
                name = key.decode(errors="ignore")
                if name in keys:
                    values[name] = value.decode(errors="ignore")
            if values.get("DISPLAY"):
                candidates.append((int(proc.name), values))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else {}


def _desktop_env() -> dict[str, str]:
    env = dict(os.environ)
    local_bin = str(Path.home() / ".local" / "bin")
    env["PATH"] = local_bin + os.pathsep + env.get("PATH", "")
    runtime = f"/run/user/{os.getuid()}"
    discovered = _active_graphics_env()
    for key, value in discovered.items():
        if value:
            env[key] = value
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("XDG_SESSION_TYPE", "wayland")
    env.setdefault("DISPLAY", ":0")
    sockets = sorted(Path(env["XDG_RUNTIME_DIR"]).glob("wayland-*"))
    if sockets and not env.get("WAYLAND_DISPLAY"):
        env["WAYLAND_DISPLAY"] = sockets[-1].name
    xauthority = Path.home() / ".Xauthority"
    if xauthority.exists():
        env.setdefault("XAUTHORITY", str(xauthority))
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    env["YDOTOOL_SOCKET"] = f"{runtime}/actis-ydotool.sock"
    return env


class _PrimedComputerTool:
    """Enter the MCP and seed its target state from the current frontmost app."""

    def __init__(self, tool: MCPStdioTool) -> None:
        self._tool = tool

    async def __aenter__(self) -> MCPStdioTool:
        entered = await self._tool.__aenter__()
        try:
            result = await entered.call_tool("get_frontmost_app")
            texts = [
                item.text
                for item in result
                if getattr(item, "type", None) == "text" and getattr(item, "text", None)
            ]
            app_id = None
            for text in texts:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                app_id = (payload.get("app") or {}).get("bundleId")
                if app_id:
                    break
            if app_id:
                await entered.call_tool("activate_app", bundle_id=app_id)
        except (ToolExecutionException, TypeError, ValueError, KeyError):
            pass

        # Some models emit optional target fields as empty defaults ("" / 0).
        # Zavora interprets those as explicit targets, overriding the primed session state.
        # Strip only invalid defaults; real app/window targets remain untouched.
        for function in getattr(entered, "functions", []):
            original = getattr(function, "func", None)
            if not callable(original):
                continue

            async def sanitized(ctx, _original=original, **kwargs):
                if not str(kwargs.get("target_app") or "").strip():
                    kwargs.pop("target_app", None)
                window_id = kwargs.get("target_window_id")
                if isinstance(window_id, (int, float)) and window_id <= 0:
                    kwargs.pop("target_window_id", None)
                if not str(kwargs.get("focus_strategy") or "").strip():
                    kwargs.pop("focus_strategy", None)
                return await _original(ctx, **kwargs)

            function.func = sanitized
        return entered

    async def __aexit__(self, exc_type, exc, tb):
        return await self._tool.__aexit__(exc_type, exc, tb)


COMPUTER_ACTION_TOOLS = (
    "policy_status", "left_click", "right_click", "double_click", "mouse_move",
    "cursor_position", "scroll", "type", "key", "hold_key", "read_clipboard",
    "write_clipboard", "open_application", "get_frontmost_app", "list_windows",
    "discover_applications", "get_display_size", "list_displays", "activate_app",
    "activate_window", "wait", "get_tool_guide", "get_app_capabilities",
    "get_tool_metadata",
)


def _computer_mcp(
    *,
    allowed_tools: tuple[str, ...] | list[str] | None = None,
    prime_focus: bool = False,
):
    env = _desktop_env()
    env["COMPUTER_USE_PROFILE"] = "core"
    tool = MCPStdioTool(
        name="harness_computer",
        command=_binary("computer-use-mcp"),
        env=env,
        load_prompts=False,
        request_timeout=60,
        approval_mode="never_require",
        allowed_tools=allowed_tools,
    )
    return _PrimedComputerTool(tool) if prime_focus else tool


COMPUTER_VIEW_TOOLS = (
    "policy_status", "cursor_position", "read_clipboard", "get_frontmost_app",
    "list_windows", "discover_applications", "get_display_size", "list_displays",
    "get_tool_guide", "get_app_capabilities", "get_tool_metadata",
)
COMPUTER_CONTROL_TOOLS = tuple(
    name for name in COMPUTER_ACTION_TOOLS if name not in COMPUTER_VIEW_TOOLS
)


def build_harness_computer(scopes: set[str] | None = None, *, run_id: str | None = None, agent_id: str | None = None) -> MCPStdioTool:
    """Zavora actions restricted by computer.view / computer.control scopes."""
    if scopes is None:
        allowed = COMPUTER_ACTION_TOOLS
    else:
        names: list[str] = []
        if "computer.view" in scopes:
            names.extend(COMPUTER_VIEW_TOOLS)
        if "computer.control" in scopes:
            names.extend(COMPUTER_VIEW_TOOLS)
            names.extend(COMPUTER_CONTROL_TOOLS)
        allowed = tuple(dict.fromkeys(names))
    return _computer_mcp(allowed_tools=allowed, prime_focus=True)


def build_computer_observer(env_file: str | None, model: str):
    """Build a textual vision bridge backed by Zavora screenshot + 9Router multimodal input."""

    async def computer_observe(request: str = "Descreva a tela atual e os controles relevantes.") -> str:
        """Observe o desktop real e retorne descrição e coordenadas de tela para a tarefa pedida."""
        async with _computer_mcp(allowed_tools=("screenshot",)) as computer:
            result = await computer.call_tool("screenshot")
        image_uri = next(
            (item.uri for item in result if getattr(item, "type", None) == "data" and item.uri),
            None,
        )
        mapping = "\n".join(
            item.text or "" for item in result if getattr(item, "type", None) == "text"
        ).strip()
        if not image_uri:
            return "Falha ao capturar a tela atual."
        vision_model = os.environ.get("ACTIS_COMPUTER_VISION_MODEL", "cx/gpt-5.6-luna")
        settings = NineRouterSettings.from_env(env_file, model=vision_model or model or None, timeout_s=60)
        prompt = (
            "Você é o subsistema visual do ACTIS Computer Use. Analise a captura do desktop. "
            "Responda em português, objetivamente, com: VISÍVEL, ALVOS relevantes e PRÓXIMA AÇÃO. "
            "Para cada alvo clicável relevante, forneça coordenadas físicas da TELA no formato (x, y). "
            "Use o mapeamento informado; não devolva coordenadas da imagem reduzida. "
            f"Tarefa do agente: {request}\nMapeamento da captura:\n{mapping}"
        )
        async with NineRouterGateway(settings).open_client() as client:
            response = await client.chat.completions.create(
                model=settings.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ],
                }],
                max_tokens=450,
            )
        message = response.choices[0].message
        text = message.content or getattr(message, "reasoning", None)
        return str(text or "A captura foi recebida, mas o modelo visual não retornou descrição.")

    return computer_observe
