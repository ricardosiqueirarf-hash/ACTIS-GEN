"""End-to-end stdio MCP smoke test for the isolated ChatGPT browser POC."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
PYTHON = "/home/lowkanta/.local/share/uv/tools/browser-harness/bin/python"
SERVER = str(HERE / "server.py")
FIXTURE = "http://127.0.0.1:8877/fixture.html"


def _text(result) -> str:
    return "\n".join(
        item.text for item in (getattr(result, "content", []) or [])
        if getattr(item, "text", None)
    )


def _json_result(result):
    payload = json.loads(_text(result))
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


async def main() -> None:
    params = StdioServerParameters(command=PYTHON, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            required = {"browser_status", "browser_goto", "browser_snapshot", "browser_click_ref", "browser_type_ref"}
            missing = sorted(required.difference(names))
            if missing:
                raise RuntimeError(f"Missing MCP tools: {missing}")

            status = _json_result(await session.call_tool("browser_status", {}))
            if status.get("agent_id") != "chatgpt-poc":
                raise RuntimeError(f"Wrong isolated profile: {status}")

            _json_result(await session.call_tool("browser_goto", {"url": FIXTURE}))
            await session.call_tool("browser_wait", {"seconds": 0.2})
            snap1 = _json_result(await session.call_tool("browser_snapshot", {"max_elements": 20}))
            elements = snap1.get("elements", []) if isinstance(snap1, dict) else []
            input_ref = next(item["ref"] for item in elements if item.get("tag") == "input")
            button_ref = next(item["ref"] for item in elements if item.get("tag") == "button")

            _json_result(await session.call_tool("browser_type_ref", {"ref": input_ref, "text": "teste-chatgpt", "clear_first": True}))
            _json_result(await session.call_tool("browser_click_ref", {"ref": button_ref}))
            snap2 = _json_result(await session.call_tool("browser_snapshot", {"max_elements": 20}))
            if "recebido:teste-chatgpt" not in snap2.get("text", ""):
                raise RuntimeError(f"Interaction failed: {snap2}")

            shot = _json_result(await session.call_tool("browser_screenshot", {}))
            print(json.dumps({
                "ok": True,
                "tool_count": len(names),
                "profile": status.get("profile_dir"),
                "url": snap2.get("url"),
                "semantic_refs": len(snap2.get("elements", [])),
                "screenshot": shot.get("path"),
                "result_text": snap2.get("text"),
            }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
