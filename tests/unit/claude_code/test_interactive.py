import asyncio
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.claude_code.interactive import (
    PLUGIN_DIR,
    InteractiveOptions,
    InteractiveRunner,
    build_interactive_args,
)
from whiletrue.claude_code.panes import ProcessPaneHost
from whiletrue.core import AgentLimits, AgentRequest, RunContext, create_builtin_caps

FAKE = r'''#!__PY__
"""A stand-in for interactive `claude`: does what the real plugin would do, per FAKE_MODE."""
import importlib.util, json, os, sys, time

args = sys.argv[1:]
out = os.environ["FAKE_OUT"]
open(out + ".args", "w").write(json.dumps(args))
open(out + ".env", "w").write(json.dumps({k: v for k, v in os.environ.items() if k.startswith("WT_")}))
plugin = args[args.index("--plugin-dir") + 1]
spec = importlib.util.spec_from_file_location("hook", plugin + "/hook.py")
hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
env = os.environ
mode = env.get("FAKE_MODE", "submit")


def post(path, body):
    return hook.post_json(env["WT_ENDPOINT"], path, body, env["WT_TOKEN"], env["WT_SESSION"])


def fire(event, payload=None):
    return post("/hook", {"event": event, "payload": payload or {}})


def tool(name, arguments):
    return post("/tools/call", {"name": name, "arguments": arguments})


def exit_on_slash_exit():
    for line in sys.stdin:
        if line.strip() == "/exit":
            break
    fire("SessionEnd", {})
    sys.exit(0)


transcript = env.get("FAKE_TRANSCRIPT")
fire("SessionStart", {"session_id": "claude-sid", "transcript_path": transcript})
fire("UserPromptSubmit", {"prompt": args[-1]})
open(out + ".prompt", "w").write(args[-1])

if mode == "die":
    sys.exit(3)
if mode == "hang":
    exit_on_slash_exit()
if mode == "bad_then_ok":
    reply = tool("whiletrue_submit", {"result": {"pass": "nope"}})
    open(out + ".first", "w").write(json.dumps(reply))
    tool("whiletrue_submit", {"result": {"pass": True}})
elif mode == "ask":
    tool("whiletrue_ask", {"question": "which database?"})
elif mode == "nudge":
    reply = fire("Stop", {"last_assistant_message": "thinking"})
    open(out + ".nudge", "w").write(json.dumps(reply))
    tool("whiletrue_submit", {"result": "late"})
elif mode == "attention":
    fire("Notification", {"message": "needs permission"})
    time.sleep(0.2)
    fire("PostToolUse", {"tool_name": "Bash"})
    tool("whiletrue_submit", {"result": "ok"})
elif mode == "overbudget":
    reply = fire("PreToolUse", {"tool_name": "Bash"})
    open(out + ".pretool", "w").write(json.dumps(reply))
    exit_on_slash_exit()
elif mode == "silent":
    pass
elif mode == "steer":
    fire("UserPromptSubmit", {"prompt": "do it differently"})
    tool("whiletrue_submit", {"result": "steered"})
elif mode == "tools":
    reply = fire("PreToolUse", {"tool_name": "Bash"})
    open(out + ".pretool", "w").write(json.dumps(reply))
    fire("PostToolUse", {"tool_name": "Bash"})
    tool("whiletrue_submit", {"result": "tooled"})
else:
    tool("whiletrue_submit", {"result": "42"})
fire("Stop", {"last_assistant_message": "all done"})
if mode == "stubborn":
    time.sleep(60)
exit_on_slash_exit()
'''.replace("__PY__", sys.executable)


def fake_claude(directory: Path) -> str:
    path = directory / "fake-claude"
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "out"))

    def make(mode: str = "submit", **options: Any) -> InteractiveRunner:
        monkeypatch.setenv("FAKE_MODE", mode)
        defaults: dict[str, Any] = {
            "poll_interval": 0.02,
            "exit_grace": 2.0,
            "limit_grace": 0.2,
            "host": ProcessPaneHost(),
        }
        return InteractiveRunner(
            InteractiveOptions(command=fake_claude(tmp_path), **{**defaults, **options})
        )

    return make


def request(directory: Path, **over: Any) -> AgentRequest:
    values: dict[str, Any] = {
        "prompt": "Do the thing",
        "cwd": str(directory),
        "step_key": "run:item:node",
        "label": "item agent",
        "session_id": "11111111-1111-1111-1111-111111111111",
    }
    values.update(over)
    return AgentRequest(**values)


def ctx(
    events: list[dict[str, Any]] | None = None, signal: AbortSignal | None = None
) -> RunContext:
    return RunContext(
        signal=signal or AbortSignal(), on_event=events.append if events is not None else None
    )


def recorded(directory: Path, suffix: str) -> Any:
    return json.loads((directory / f"out.{suffix}").read_text())


async def stop(runner: InteractiveRunner) -> None:
    if runner._endpoint is not None:
        await runner._endpoint.stop()


# ---- argv ----------------------------------------------------------------------------------


def flags(req: AgentRequest) -> list[str]:
    return build_interactive_args(
        req, plugin_dir="/p", mcp_config="/m.json", system_prompt_file="/s.md"
    )


def test_maps_every_request_field_to_a_flag() -> None:
    args = flags(
        AgentRequest(
            prompt="x",
            cwd=".",
            model="opus",
            allowed_tools=["Read", "Bash(git *)"],
            disallowed_tools=["WebFetch"],
            tools=["Read", "Edit"],
            permission_mode="acceptEdits",
            session_id="sid",
            add_dirs=["/specs", "/docs"],
            bare=True,
        )
    )
    assert args[:6] == [
        "--plugin-dir",
        "/p",
        "--mcp-config",
        "/m.json",
        "--append-system-prompt-file",
        "/s.md",
    ]
    assert args[args.index("--session-id") + 1] == "sid"
    assert args[args.index("--model") + 1] == "opus"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools=Read,Bash(git *),mcp__whiletrue" in args
    assert "--disallowedTools=WebFetch" in args
    assert "--tools=Read,Edit" in args
    assert args.count("--add-dir") == 2 and "/docs" in args
    assert args[-1] == "--bare"


def test_resume_and_fork_flags() -> None:
    resumed = flags(AgentRequest(prompt="x", cwd=".", resume="old"))
    assert resumed[resumed.index("--resume") + 1] == "old"
    assert "--fork-session" not in resumed and "--session-id" not in resumed
    forked = flags(AgentRequest(prompt="x", cwd=".", resume="old", fork=True))
    assert "--fork-session" in forked


def test_the_minimal_request_only_allows_the_whiletrue_tools() -> None:
    args = flags(AgentRequest(prompt="x", cwd="."))
    assert "--allowedTools=mcp__whiletrue" in args
    assert not any(a.startswith(("--disallowedTools", "--tools")) for a in args)


# ---- runs ----------------------------------------------------------------------------------


async def test_a_submitted_result_finishes_the_step(setup: Any, tmp_path: Path) -> None:
    runner = setup("submit")
    events: list[dict[str, Any]] = []
    result = await runner.run(request(tmp_path), ctx(events))
    await stop(runner)
    assert result.ok and result.output == "42" and result.text == "all done"
    assert result.session_id == "claude-sid"
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "session_start" and "submit" in kinds and kinds[-1] == "done"


async def test_the_session_is_launched_with_the_plugin_mcp_and_prompt(
    setup: Any, tmp_path: Path
) -> None:
    runner = setup("submit")
    await runner.run(request(tmp_path, model="sonnet"), ctx())
    await stop(runner)
    args = recorded(tmp_path, "args")
    assert args[args.index("--plugin-dir") + 1] == str(PLUGIN_DIR)
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[-2:] == ["--", "Do the thing"]
    mcp = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
    server = mcp["mcpServers"]["whiletrue"]
    assert server["args"][0].endswith("mcp_server.py")
    assert server["env"]["WT_SESSION"] == "run:item:node"
    system = Path(args[args.index("--append-system-prompt-file") + 1]).read_text()
    assert "whiletrue_submit" in system
    env = recorded(tmp_path, "env")
    assert env["WT_SESSION"] == "run:item:node" and env["WT_ENDPOINT"].startswith(
        "http://127.0.0.1:"
    )
    assert env["WT_PYTHON"] == sys.executable


async def test_prompts_and_user_system_prompt_files_are_passed_through(
    setup: Any, tmp_path: Path
) -> None:
    rules = tmp_path / "rules.md"
    rules.write_text("Always use uv.")
    prompt = "word " * 10_000
    runner = setup("submit")
    await runner.run(request(tmp_path, prompt=prompt, append_system_prompt_file=str(rules)), ctx())
    await stop(runner)
    assert (tmp_path / "out.prompt").read_text() == prompt
    args = recorded(tmp_path, "args")
    system = Path(args[args.index("--append-system-prompt-file") + 1]).read_text()
    assert "whiletrue_submit" in system and "Always use uv." in system


async def test_a_prompt_too_big_for_one_argument_is_passed_as_a_file(
    setup: Any, tmp_path: Path
) -> None:
    prompt = "word " * 50_000  # 250 KB: over Linux's per-argument limit
    runner = setup("submit")
    await runner.run(request(tmp_path, prompt=prompt), ctx())
    await stop(runner)
    pointer = (tmp_path / "out.prompt").read_text()
    assert "prompt.md" in pointer and len(pointer) < 500
    path = pointer.split("`")[1]
    assert Path(path).read_text() == prompt


async def test_extra_mcp_configs_and_args_are_appended(setup: Any, tmp_path: Path) -> None:
    runner = setup("submit", extra_args=["--debug"])
    await runner.run(request(tmp_path, mcp_config=["/extra.json"]), ctx())
    await stop(runner)
    args = recorded(tmp_path, "args")
    assert args.count("--mcp-config") == 2 and "/extra.json" in args and "--debug" in args


async def test_an_invalid_result_is_returned_to_the_agent_to_fix(
    setup: Any, tmp_path: Path
) -> None:
    runner = setup("bad_then_ok")
    req = request(
        tmp_path,
        output_cap="Verdict",
        output_schema=create_builtin_caps().to_json_schema("Verdict"),
        validate_output=lambda v: create_builtin_caps().validate("Verdict", v),
    )
    result = await runner.run(req, ctx())
    await stop(runner)
    assert result.ok and result.output == {"pass": True, "reason": None}
    assert recorded(tmp_path, "first")["is_error"] is True


async def test_stop_without_a_result_nudges_the_agent(setup: Any, tmp_path: Path) -> None:
    runner = setup("nudge")
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert result.ok and result.output == "late"
    assert recorded(tmp_path, "nudge")["decision"] == "block"


async def test_a_silent_agent_is_parked_after_the_nudges(setup: Any, tmp_path: Path) -> None:
    # "silent" fires one Stop per nudge until they run out.
    runner = setup("silent", max_nudges=0)
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert not result.ok and result.park == "needs_human"
    assert "without submitting" in (result.error or "")


async def test_ask_parks_the_step_with_the_question(setup: Any, tmp_path: Path) -> None:
    runner = setup("ask")
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert not result.ok and result.park == "needs_human" and result.error == "which database?"


async def test_steering_is_reported_with_the_cost_split(setup: Any, tmp_path: Path) -> None:
    runner = setup("steer")
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert result.ok and result.steered and result.steered_cost_usd == 0


async def test_unsteered_results_carry_no_split(setup: Any, tmp_path: Path) -> None:
    runner = setup("submit")
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert not result.steered and result.steered_cost_usd is None


async def test_tool_calls_pass_through_pre_tool_use_when_within_limits(
    setup: Any, tmp_path: Path
) -> None:
    runner = setup("tools")
    result = await runner.run(request(tmp_path, limits=AgentLimits(turns=5)), ctx())
    await stop(runner)
    assert (result.ok and recorded(tmp_path, "pretool") is None) or recorded(
        tmp_path, "pretool"
    ) == {}


async def test_a_dead_pane_without_a_result_fails_the_step(setup: Any, tmp_path: Path) -> None:
    runner = setup("die")
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert not result.ok and result.park is None
    assert "without submitting" in (result.error or "")


async def test_the_wall_limit_parks_a_hanging_session(setup: Any, tmp_path: Path) -> None:
    runner = setup("hang")
    result = await runner.run(request(tmp_path, limits=AgentLimits(wall_ms=300)), ctx())
    await stop(runner)
    assert not result.ok and result.park == "wall_exceeded"


async def test_abort_kills_the_session(setup: Any, tmp_path: Path) -> None:
    runner = setup("hang")
    signal = AbortSignal()
    task = asyncio.ensure_future(runner.run(request(tmp_path), ctx(signal=signal)))
    await asyncio.sleep(0.3)
    signal.abort()
    result = await asyncio.wait_for(task, 5)
    await stop(runner)
    assert not result.ok and result.error == "aborted"


async def test_an_already_aborted_run_does_not_start(setup: Any, tmp_path: Path) -> None:
    runner = setup("submit")
    signal = AbortSignal()
    signal.abort()
    result = await runner.run(request(tmp_path), ctx(signal=signal))
    assert result.error == "aborted" and not (tmp_path / "out.args").exists()


async def test_a_stubborn_session_is_killed_after_the_exit_grace(
    setup: Any, tmp_path: Path
) -> None:
    runner = setup("stubborn", exit_grace=0.2)
    result = await asyncio.wait_for(runner.run(request(tmp_path), ctx()), 10)
    await stop(runner)
    assert result.ok


async def test_a_missing_binary_is_reported(tmp_path: Path) -> None:
    runner = InteractiveRunner(
        InteractiveOptions(
            command=str(tmp_path / "nope"), host=ProcessPaneHost(), poll_interval=0.02
        )
    )
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert not result.ok


async def test_a_host_that_cannot_open_a_pane_is_reported(tmp_path: Path) -> None:
    class Broken:
        async def open(self, **kwargs: Any) -> Any:
            raise RuntimeError("tmux is not running")

    runner = InteractiveRunner(InteractiveOptions(host=Broken()))
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert result.error == "Failed to start a pane: tmux is not running"


async def test_an_unreadable_system_prompt_file_is_reported(setup: Any, tmp_path: Path) -> None:
    runner = setup("submit")
    result = await runner.run(
        request(tmp_path, append_system_prompt_file=str(tmp_path / "missing.md")), ctx()
    )
    await stop(runner)
    assert result.error is not None and result.error.startswith("Cannot prepare the session")


class RecordingHost(ProcessPaneHost):
    def __init__(self) -> None:
        self.titles: list[str] = []

    async def open(self, **kwargs: Any) -> Any:
        pane = await super().open(**kwargs)
        real = pane.set_title
        titles = self.titles

        async def set_title(title: str) -> None:
            titles.append(title)
            await real(title)

        pane.set_title = set_title
        return pane


async def test_a_notification_flags_the_pane_until_the_agent_moves_on(
    setup: Any, tmp_path: Path
) -> None:
    host = RecordingHost()
    runner = setup("attention", host=host)
    result = await runner.run(request(tmp_path), ctx())
    await stop(runner)
    assert result.ok
    assert "! item agent needs you" in host.titles
    assert host.titles.index("item agent") > host.titles.index("! item agent needs you")


async def test_blowing_the_budget_denies_tools_and_parks_a_session_that_will_not_stop(
    setup: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-23T09:00:00Z",
                "message": {
                    "id": "m1",
                    "model": "claude-sonnet-5-5",
                    "usage": {"output_tokens": 1_000_000},
                },
            }
        )
        + "\n"
    )
    monkeypatch.setenv("FAKE_TRANSCRIPT", str(transcript))
    runner = setup("overbudget")
    result = await runner.run(request(tmp_path, limits=AgentLimits(budget_usd=0.5)), ctx())
    await stop(runner)
    assert not result.ok and result.park == "budget_exceeded"
    denial = recorded(tmp_path, "pretool")["hookSpecificOutput"]
    assert denial["permissionDecision"] == "deny"
    assert result.cost_usd is not None and result.cost_usd > 0.5
