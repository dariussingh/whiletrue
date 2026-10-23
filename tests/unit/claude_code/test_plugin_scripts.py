import asyncio
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from whiletrue.claude_code.endpoint import EventEndpoint, ToolResult

PLUGIN = Path(__file__).resolve().parents[3] / "whiletrue" / "claude_code" / "plugin"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"wt_plugin_{name}", PLUGIN / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load("hook")
mcp = load("mcp_server")


class Handler:
    def __init__(self) -> None:
        self.hooks: list[tuple[str, dict[str, Any]]] = []

    async def on_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.hooks.append((event, payload))
        return {"decision": "block", "reason": "nudge"} if event == "Stop" else None

    def tools(self) -> list[dict[str, Any]]:
        return [{"name": "whiletrue_submit", "description": "d", "inputSchema": {"type": "object"}}]

    async def on_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(f"got {args}", is_error=name == "bad")


@pytest.fixture
async def live():
    endpoint = EventEndpoint()
    await endpoint.start()
    handler = Handler()
    endpoint.register("s1", handler)
    yield endpoint, handler
    await endpoint.stop()


def run_hook(env: dict[str, str], argv: list[str], payload: str) -> tuple[int, str]:
    out = io.StringIO()
    code = hook.main(argv, io.StringIO(payload), out, env)
    return code, out.getvalue()


async def test_hook_posts_the_payload_and_prints_the_answer(live: Any) -> None:
    endpoint, handler = live
    env = endpoint.env_for("s1")
    code, out = await asyncio.to_thread(run_hook, env, ["hook.py", "Stop"], '{"a": 1}')
    assert code == 0 and json.loads(out) == {"decision": "block", "reason": "nudge"}
    assert handler.hooks == [("Stop", {"a": 1})]
    code, out = await asyncio.to_thread(run_hook, env, ["hook.py", "SessionStart"], "")
    assert (code, out) == (0, "")


def test_hook_never_breaks_the_session() -> None:
    gone = {"WT_ENDPOINT": "http://127.0.0.1:9", "WT_TOKEN": "t", "WT_SESSION": "s"}
    assert run_hook(gone, ["hook.py", "Stop"], "{}") == (0, "")
    assert run_hook(gone, ["hook.py", "Stop"], "not json") == (0, "")
    assert run_hook({}, ["hook.py", "Stop"], "{}") == (0, "")
    assert run_hook(gone, ["hook.py"], "{}") == (0, "")


def rpc(env: dict[str, str], *messages: dict[str, Any]) -> list[dict[str, Any]]:
    out = io.StringIO()
    lines = "\n".join(json.dumps(m) for m in messages) + "\n\nnot json\n[1]\n"
    mcp.serve(io.StringIO(lines), out, env)
    return [json.loads(line) for line in out.getvalue().splitlines()]


async def test_mcp_server_forwards_tools_to_the_endpoint(live: Any) -> None:
    endpoint, _ = live
    env = endpoint.env_for("s1")
    replies = await asyncio.to_thread(
        rpc,
        env,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "X"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ok", "arguments": {"result": 1}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bad"}},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
    )
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6]
    assert replies[0]["result"]["protocolVersion"] == "X"
    assert replies[1]["result"]["tools"][0]["name"] == "whiletrue_submit"
    assert replies[2]["result"] == {
        "content": [{"type": "text", "text": "got {'result': 1}"}],
        "isError": False,
    }
    assert replies[3]["result"]["isError"] is True
    assert replies[4]["result"] == {}
    assert replies[5]["error"]["code"] == -32601


def test_mcp_server_reports_an_unreachable_whiletrue() -> None:
    gone = {"WT_ENDPOINT": "http://127.0.0.1:9", "WT_TOKEN": "t", "WT_SESSION": "s"}
    replies = rpc(
        gone,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whiletrue_submit"}},
        {"jsonrpc": "2.0", "id": 3, "method": "initialize"},
    )
    assert replies[0]["result"] == {"tools": []}
    assert replies[1]["result"]["isError"] is True
    assert "unreachable" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["protocolVersion"] == mcp.PROTOCOL_FALLBACK


def test_the_scripts_run_standalone() -> None:
    """Smoke test the way Claude Code launches them: a fresh interpreter, no whiletrue import."""
    done = subprocess.run(
        [sys.executable, "-I", str(PLUGIN / "hook.py"), "Stop"],
        input="{}",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert done.returncode == 0 and done.stdout == ""
    server = subprocess.run(
        [sys.executable, "-I", str(PLUGIN / "mcp_server.py")],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert json.loads(server.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_the_plugin_manifest_wires_every_hook_to_the_script() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "Notification",
        "Stop",
        "SessionEnd",
    }
    for event, entries in hooks.items():
        command = entries[0]["hooks"][0]["command"]
        assert command.endswith(f'hook.py" {event}')
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "whiletrue"
