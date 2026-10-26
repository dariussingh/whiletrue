import asyncio
import stat
from pathlib import Path
from typing import Any

from whiletrue.abort import AbortSignal
from whiletrue.claude_code import (
    HeadlessOptions,
    build_headless_args,
    claude_code_plugin,
    create_headless_runner,
)
from whiletrue.core import AgentLimits, AgentRequest, Registry, RunContext

SUCCESS = (
    '{"type":"result","subtype":"success","is_error":false,"result":"done",'
    '"structured_output":{"pass":true},"total_cost_usd":0.5,"num_turns":2,"session_id":"s1"}'
)


def fake_claude(directory: Path, name: str, body: str) -> str:
    """A fake `claude`: dumps its argv + stdin to files, then runs the given shell body."""
    p = directory / name
    p.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{p}.args"\ncat > "{p}.stdin"\n{body}\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def req(directory: Path | str = ".", **over: Any) -> AgentRequest:
    return AgentRequest(prompt="do it", cwd=str(directory), **over)


def ctx(
    signal: AbortSignal | None = None, events: list[dict[str, Any]] | None = None
) -> RunContext:
    return RunContext(
        signal=signal or AbortSignal(),
        on_event=events.append if events is not None else None,
    )


def runner(command: str):
    return create_headless_runner(HeadlessOptions(command=command))


def test_builds_the_minimal_argv() -> None:
    assert build_headless_args(req()) == [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompts",
        "none",
    ]


def test_maps_every_request_field_to_a_flag() -> None:
    args = build_headless_args(
        req(
            model="opus",
            allowed_tools=["Read", "Bash(npm test*)"],
            disallowed_tools=["Write"],
            tools=["Read"],
            permission_mode="acceptEdits",
            system_prompt_append="be brief",
            output_schema={"type": "object"},
            limits=AgentLimits(turns=7, budget_usd=2.5),
            session_id="abc",
        ),
        ["--bare"],
    )
    expected = [
        "--model", "opus",
        "--allowedTools", "Read,Bash(npm test*)",
        "--disallowedTools", "Write",
        "--tools", "Read",
        "--permission-mode", "acceptEdits",
        "--append-system-prompt", "be brief",
        "--json-schema", '{"type":"object"}',
        "--max-turns", "7",
        "--max-budget-usd", "2.5",
        "--session-id", "abc",
        "--bare",
    ]  # fmt: skip
    assert args[6:] == expected


def test_formats_whole_number_budgets_without_a_decimal() -> None:
    args = build_headless_args(req(limits=AgentLimits(budget_usd=2.0)))
    assert args[-1] == "2"


def test_prefers_resume_over_session_id() -> None:
    args = build_headless_args(req(resume="old", session_id="new"))
    assert "--resume" in args
    assert "--session-id" not in args


async def test_parses_the_stream_returns_the_result_and_sends_the_prompt_on_stdin(
    tmp_path: Path,
) -> None:
    cmd = fake_claude(
        tmp_path,
        "ok.sh",
        """echo '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use","name":"Read"}]}}'
echo 'not json'
echo '[1]'
echo '{"type":"system"}'
echo '%s'"""
        % SUCCESS,
    )
    events: list[dict[str, Any]] = []
    r = await runner(cmd).run(req(tmp_path), ctx(events=events))
    assert (r.ok, r.text, r.output, r.session_id, r.cost_usd, r.turns, r.error) == (
        True,
        "done",
        {"pass": True},
        "s1",
        0.5,
        2,
        None,
    )
    assert events == [
        {"kind": "text"},
        {"kind": "tool_use", "name": "Read"},
        {"kind": "result", "subtype": "success"},
    ]
    assert Path(f"{cmd}.stdin").read_text() == "do it"
    assert "stream-json" in Path(f"{cmd}.args").read_text()


async def test_every_raw_message_goes_to_the_raw_tap(tmp_path: Path) -> None:
    cmd = fake_claude(
        tmp_path,
        "raw.sh",
        """echo '{"type":"system","subtype":"init"}'
echo 'not json'
echo '%s'"""
        % SUCCESS,
    )
    raw: list[dict[str, Any]] = []
    context = ctx()
    context.on_raw = raw.append
    assert (await runner(cmd).run(req(tmp_path), context)).ok
    assert [m["type"] for m in raw] == ["system", "result"]


async def test_works_without_an_event_callback(tmp_path: Path) -> None:
    cmd = fake_claude(tmp_path, "quiet.sh", f"echo '{SUCCESS}'")
    assert (await runner(cmd).run(req(tmp_path), ctx())).ok


async def test_reports_error_results_with_their_reason(tmp_path: Path) -> None:
    cmd = fake_claude(
        tmp_path,
        "err.sh",
        """echo '{"type":"result","subtype":"error_max_turns","is_error":true,"errors":["Reached maximum number of turns (1)"],"num_turns":2}'""",
    )
    r = await runner(cmd).run(req(tmp_path), ctx())
    assert (r.ok, r.error, r.turns) == (False, "Reached maximum number of turns (1)", 2)


async def test_falls_back_to_the_subtype_or_a_generic_message(tmp_path: Path) -> None:
    a = fake_claude(
        tmp_path,
        "e2.sh",
        """echo '{"type":"result","subtype":"error_during_execution","is_error":true}'""",
    )
    assert (await runner(a).run(req(tmp_path), ctx())).error == "error_during_execution"
    b = fake_claude(tmp_path, "e3.sh", """echo '{"type":"result","is_error":true}'""")
    assert (await runner(b).run(req(tmp_path), ctx())).error == "claude reported an error"


async def test_fails_when_the_process_exits_without_a_result_including_stderr(
    tmp_path: Path,
) -> None:
    cmd = fake_claude(tmp_path, "crash.sh", "echo 'auth failed' >&2; exit 3")
    r = await runner(cmd).run(req(tmp_path), ctx())
    assert not r.ok
    assert r.error is not None
    assert r.error.endswith("exited with code 3 without a result: auth failed")


async def test_omits_stderr_detail_when_there_is_none(tmp_path: Path) -> None:
    cmd = fake_claude(tmp_path, "silent.sh", "exit 1")
    r = await runner(cmd).run(req(tmp_path), ctx())
    assert r.error is not None
    assert r.error.endswith("exited with code 1 without a result")


async def test_enforces_the_wall_time_limit(tmp_path: Path) -> None:
    cmd = fake_claude(tmp_path, "slow.sh", "sleep 5")
    r = await runner(cmd).run(req(tmp_path, limits=AgentLimits(wall_ms=100)), ctx())
    assert (r.ok, r.error) == (False, "wall-time limit exceeded")


async def test_kills_grandchildren_that_hold_the_pipes_open(tmp_path: Path) -> None:
    cmd = fake_claude(tmp_path, "grand.sh", "sleep 30 & wait")
    loop = asyncio.get_running_loop()
    started = loop.time()
    r = await runner(cmd).run(req(tmp_path, limits=AgentLimits(wall_ms=100)), ctx())
    assert r.error == "wall-time limit exceeded"
    assert loop.time() - started < 3


async def test_aborts_on_signal(tmp_path: Path) -> None:
    cmd = fake_claude(tmp_path, "hang.sh", "sleep 5")
    signal = AbortSignal()
    asyncio.get_running_loop().call_later(0.1, signal.abort)
    r = await runner(cmd).run(req(tmp_path), ctx(signal))
    assert (r.ok, r.error) == (False, "aborted")


async def test_aborts_before_start(tmp_path: Path) -> None:
    signal = AbortSignal()
    signal.abort()
    r = await runner(str(tmp_path / "slow.sh")).run(req(tmp_path), ctx(signal))
    assert (r.ok, r.error) == (False, "aborted")


async def test_reports_a_missing_binary(tmp_path: Path) -> None:
    r = await runner("definitely-no-claude-here").run(req(tmp_path), ctx())
    assert r.error is not None
    assert "Failed to start definitely-no-claude-here" in r.error


def test_defaults_to_the_claude_command_and_declares_capabilities() -> None:
    r = create_headless_runner()
    assert r.id == "claude-code-headless"
    caps = r.capabilities
    assert (caps.interactive, caps.structured_output, caps.resume, caps.fork, caps.budget) == (
        False,
        True,
        True,
        False,
        True,
    )


def test_plugin_registers_the_headless_runner() -> None:
    reg = Registry()
    reg.use(claude_code_plugin())
    assert reg.get_runner("claude-code-headless") is not None


def test_plugin_also_registers_the_interactive_runner() -> None:
    reg = Registry()
    reg.use(claude_code_plugin())
    runner = reg.get_runner("claude-code")
    assert runner is not None and runner.capabilities.interactive
