import asyncio
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.codex import CodexOptions, CodexRunner, build_codex_args, codex_plugin
from whiletrue.core import AgentLimits, AgentRequest, Registry, RunContext
from whiletrue.tmux import ProcessPaneHost


def line(**event: Any) -> str:
    return json.dumps(event)


def fake_codex(directory: Path, lines: list[str], *, code: int = 0) -> str:
    """A stand-in `codex`: records argv and stdin, then prints the given JSONL events."""
    out = directory / "events.jsonl"
    out.write_text("\n".join(lines) + "\n")
    path = directory / "fake-codex"
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{directory}/args'\ncat > '{directory}/stdin'\n"
        f"cat '{out}'\nexit {code}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def req(directory: Path, **over: Any) -> AgentRequest:
    return AgentRequest(prompt="do the thing", cwd=str(directory), **over)


def ctx(
    events: list[dict[str, Any]] | None = None, signal: AbortSignal | None = None
) -> RunContext:
    return RunContext(
        signal=signal or AbortSignal(), on_event=events.append if events is not None else None
    )


SUCCESS = [
    line(type="thread.started", thread_id="th_1"),
    line(type="turn.started"),
    line(type="item.completed", item={"type": "command_execution", "command": "ls"}),
    line(type="item.completed", item={"type": "file_change", "changes": [{"path": "a.py"}]}),
    line(type="item.completed", item={"type": "mcp_tool_call", "tool": "search"}),
    line(type="item.completed", item={"type": "reasoning", "text": "hmm"}),
    line(type="item.completed", item={"type": "agent_message", "text": "All done."}),
    line(type="turn.completed", usage={"input_tokens": 10, "output_tokens": 3}),
    "not json",
    "[1]",
]


def test_a_fresh_session_uses_flags() -> None:
    args = build_codex_args(
        AgentRequest(prompt="p", cwd="/w", model="gpt-x", permission_mode="plan"),
        schema_file="/s.json",
        extra_args=["--color", "never"],
    )
    assert args == [
        "exec", "--json", "--skip-git-repo-check", "-C", "/w", "-s", "read-only",
        "-m", "gpt-x", "--output-schema", "/s.json", "--color", "never", "-",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("mode", "sandbox"),
    [
        (None, "workspace-write"),
        ("acceptEdits", "workspace-write"),
        ("bypassPermissions", "danger-full-access"),
        ("unheard-of", "workspace-write"),
    ],
)
def test_permission_modes_map_to_sandboxes(mode: str | None, sandbox: str) -> None:
    args = build_codex_args(AgentRequest(prompt="p", cwd=".", permission_mode=mode))
    assert args[args.index("-s") + 1] == sandbox


def test_resume_and_fork_use_subcommands_and_config_overrides() -> None:
    resumed = build_codex_args(AgentRequest(prompt="p", cwd=".", resume="th_9", model="m"))
    assert resumed[:3] == ["exec", "resume", "th_9"]
    assert 'sandbox_mode="workspace-write"' in resumed and 'model="m"' in resumed
    assert "-C" not in resumed and resumed[-1] == "-"
    forked = build_codex_args(AgentRequest(prompt="p", cwd=".", resume="th_9", fork=True))
    assert forked[:3] == ["exec", "fork", "th_9"]


async def test_collects_the_final_message_session_and_tool_events(tmp_path: Path) -> None:
    command = fake_codex(tmp_path, SUCCESS)
    events: list[dict[str, Any]] = []
    result = await CodexRunner(CodexOptions(command=command)).run(req(tmp_path), ctx(events))
    assert result.ok and result.text == "All done."
    assert (result.session_id, result.turns, result.cost_usd) == ("th_1", 1, None)
    assert [e["kind"] for e in events] == ["tool_use", "tool_use", "tool_use", "text"]
    assert [e.get("name") for e in events[:3]] == ["shell", "apply_patch", "search"]
    assert (tmp_path / "stdin").read_text() == "do the thing"


async def test_structured_output_uses_a_closed_schema_file_and_parses_the_reply(
    tmp_path: Path,
) -> None:
    reply = line(type="item.completed", item={"type": "agent_message", "text": '{"pass": true}'})
    command = fake_codex(tmp_path, [line(type="thread.started", thread_id="t"), reply])
    schema = {
        "type": "object",
        "properties": {"pass": {"type": "boolean"}},
        "$defs": {"X": {"type": "object", "properties": {}}},
    }
    result = await CodexRunner(CodexOptions(command=command)).run(
        req(tmp_path, output_schema=schema), ctx()
    )
    assert result.ok and result.output == {"pass": True}
    args = (tmp_path / "args").read_text().splitlines()
    written = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
    assert written["additionalProperties"] is False and written["required"] == ["pass"]
    assert written["$defs"]["X"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # the caller's schema is untouched

    plain = fake_codex(
        tmp_path,
        [line(type="item.completed", item={"type": "agent_message", "text": "no json here"})],
    )
    failed = await CodexRunner(CodexOptions(command=plain)).run(
        req(tmp_path, output_schema=schema), ctx()
    )
    assert not failed.ok and "no JSON" in (failed.error or "")


async def test_an_unauthenticated_codex_reports_the_failure_it_printed(tmp_path: Path) -> None:
    # The stream a logged-out Codex really produces.
    command = fake_codex(
        tmp_path,
        [
            line(type="thread.started", thread_id="th_x"),
            line(type="turn.started"),
            line(
                type="error",
                message="Reconnecting... 2/5 (workspace routing discovery unauthorized (401))",
            ),
            line(
                type="turn.failed",
                error={"message": "workspace routing discovery unauthorized (401)"},
            ),
        ],
        code=1,
    )
    result = await CodexRunner(CodexOptions(command=command)).run(req(tmp_path), ctx())
    assert not result.ok
    assert result.error == "codex failed: workspace routing discovery unauthorized (401)"
    assert result.session_id == "th_x"


async def test_a_nonzero_exit_without_events_still_fails(tmp_path: Path) -> None:
    command = fake_codex(tmp_path, [line(type="error", message="boom")], code=2)
    result = await CodexRunner(CodexOptions(command=command)).run(req(tmp_path), ctx())
    assert not result.ok and "boom" in (result.error or "")


async def test_missing_binary_abort_and_wall_time(tmp_path: Path) -> None:
    missing = CodexRunner(CodexOptions(command=str(tmp_path / "nope")))
    assert "Failed to start" in (await missing.run(req(tmp_path), ctx())).error  # type: ignore[operator]
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    runner = CodexRunner(CodexOptions(command=str(slow)))
    assert (await runner.run(req(tmp_path, limits=AgentLimits(wall_ms=200)), ctx())).error == (
        "wall-time limit exceeded"
    )
    signal = AbortSignal()
    task = asyncio.ensure_future(runner.run(req(tmp_path), ctx(signal=signal)))
    await asyncio.sleep(0.2)
    signal.abort()
    assert (await asyncio.wait_for(task, 5)).error == "aborted"
    done = AbortSignal()
    done.abort()
    assert (await runner.run(req(tmp_path), ctx(signal=done))).error == "aborted"


async def test_the_stream_is_mirrored_to_a_pane(tmp_path: Path) -> None:
    command = fake_codex(tmp_path, SUCCESS)
    runner = CodexRunner(CodexOptions(command=command, host=ProcessPaneHost()))
    result = await runner.run(req(tmp_path, label="#1 codex", step_key="r:1:n"), ctx())
    assert result.ok
    [log] = list((tmp_path / ".whiletrue" / "sessions").glob("*/stream.log"))
    text = log.read_text()
    assert "$ ls" in text and "All done." in text and "[file change]" in text
    assert Path(str(log) + ".done").exists()


def test_the_plugin_registers_the_runner() -> None:
    registry = Registry()
    registry.use(codex_plugin())
    runner = registry.get_runner("codex")
    assert runner is not None and runner.capabilities.fork and not runner.capabilities.budget
