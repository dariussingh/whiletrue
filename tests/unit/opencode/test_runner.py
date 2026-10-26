import asyncio
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.basics.json_extract import extract_json
from whiletrue.core import AgentLimits, AgentRequest, Registry, RunContext
from whiletrue.opencode import (
    OpencodeOptions,
    OpencodeRunner,
    build_opencode_args,
    opencode_plugin,
)
from whiletrue.tmux import ProcessPaneHost


def event(kind: str, **part: Any) -> str:
    return json.dumps({"type": kind, "sessionID": "ses_1", "part": part})


def fake_opencode(directory: Path, lines: list[str], *, code: int = 0, stderr: str = "") -> str:
    path = directory / "fake-opencode"
    out = directory / "out.jsonl"
    out.write_text("\n".join(lines) + "\n")
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{directory}/args'\ncat '{out}'\n"
        f"[ -n \"{stderr}\" ] && echo '{stderr}' >&2\nexit {code}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def req(directory: Path, **over: Any) -> AgentRequest:
    return AgentRequest(prompt="review this", cwd=str(directory), **over)


def ctx(
    events: list[dict[str, Any]] | None = None, signal: AbortSignal | None = None
) -> RunContext:
    return RunContext(
        signal=signal or AbortSignal(), on_event=events.append if events is not None else None
    )


def test_builds_the_argv() -> None:
    args = build_opencode_args(
        AgentRequest(prompt="p", cwd="/w", model="anthropic/x", resume="ses_9", fork=True),
        ["--thinking"],
    )
    assert args == [
        "run", "--format", "json", "--dir", "/w", "--model", "anthropic/x",
        "--session", "ses_9", "--fork", "--thinking", "--", "p",
    ]  # fmt: skip
    plain = build_opencode_args(AgentRequest(prompt="p", cwd="."))
    assert "--session" not in plain and plain[-2:] == ["--", "p"]


def test_a_schema_is_requested_in_the_prompt() -> None:
    args = build_opencode_args(AgentRequest(prompt="p", cwd=".", output_schema={"type": "object"}))
    assert "ONLY a JSON value" in args[-1] and '{"type": "object"}' in args[-1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"pass": true}', {"pass": True}),
        ('Here you go:\n```json\n{"a": [1, 2]}\n```\nbye', {"a": [1, 2]}),
        ("noise [1, 2] more", [1, 2]),
        ("no json at all", None),
        ("{broken", None),
        ('```\n{"x": 1}\n```\ntrailing {"y": 2}', {"x": 1}),
    ],
)
def test_extracts_json_from_replies(text: str, expected: Any) -> None:
    assert extract_json(text) == expected


async def test_collects_text_cost_session_and_events(tmp_path: Path) -> None:
    command = fake_opencode(
        tmp_path,
        [
            event("step_start"),
            event("text", text="Looks "),
            event("tool_use", tool="read"),
            event("text", text="good."),
            event("step_finish", cost=0.02, tokens={"total": 5}),
            "not json",
            "[1]",
            event("step_finish", cost=0.01),
        ],
    )
    events: list[dict[str, Any]] = []
    runner = OpencodeRunner(OpencodeOptions(command=command))
    result = await runner.run(req(tmp_path), ctx(events))
    assert result.ok and result.text == "Looks good."
    assert (result.session_id, result.turns) == ("ses_1", 2)
    assert result.cost_usd == pytest.approx(0.03)
    assert [e["kind"] for e in events] == ["text", "tool_use", "text"]
    assert events[1]["name"] == "read"
    args = (tmp_path / "args").read_text().splitlines()
    assert args[:3] == ["run", "--format", "json"] and args[-1] == "review this"


async def test_structured_output_is_parsed_from_the_reply(tmp_path: Path) -> None:
    command = fake_opencode(tmp_path, [event("text", text='```json\n{"pass": false}\n```')])
    runner = OpencodeRunner(OpencodeOptions(command=command))
    schema = {"type": "object"}
    result = await runner.run(req(tmp_path, output_schema=schema), ctx())
    assert result.ok and result.output == {"pass": False}
    bad = fake_opencode(tmp_path, [event("text", text="I refuse to use JSON")])
    failed = await OpencodeRunner(OpencodeOptions(command=bad)).run(
        req(tmp_path, output_schema=schema), ctx()
    )
    assert not failed.ok and "no JSON" in (failed.error or "")


async def test_failures_are_reported(tmp_path: Path) -> None:
    crashed = fake_opencode(tmp_path, [], code=3, stderr="provider not configured")
    result = await OpencodeRunner(OpencodeOptions(command=crashed)).run(req(tmp_path), ctx())
    assert not result.ok and "code 3" in (result.error or "") and "provider" in (result.error or "")
    errored = fake_opencode(tmp_path, [event("error").replace('"part"', '"error"')])
    result = await OpencodeRunner(OpencodeOptions(command=errored)).run(req(tmp_path), ctx())
    assert not result.ok
    missing = OpencodeRunner(OpencodeOptions(command=str(tmp_path / "nope")))
    assert "Failed to start" in (await missing.run(req(tmp_path), ctx())).error  # type: ignore[operator]


async def test_abort_and_wall_time_stop_the_process(tmp_path: Path) -> None:
    slow = tmp_path / "slow-opencode"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    runner = OpencodeRunner(OpencodeOptions(command=str(slow)))
    timed = await runner.run(req(tmp_path, limits=AgentLimits(wall_ms=200)), ctx())
    assert timed.error == "wall-time limit exceeded"
    signal = AbortSignal()
    task = asyncio.ensure_future(runner.run(req(tmp_path), ctx(signal=signal)))
    await asyncio.sleep(0.2)
    signal.abort()
    assert (await asyncio.wait_for(task, 5)).error == "aborted"
    pre = AbortSignal()
    pre.abort()
    assert (await runner.run(req(tmp_path), ctx(signal=pre))).error == "aborted"


async def test_the_stream_is_mirrored_to_a_read_only_pane(tmp_path: Path) -> None:
    command = fake_opencode(
        tmp_path, [event("text", text="line one"), event("tool_use", tool="grep")]
    )
    titles: list[str] = []

    class Host(ProcessPaneHost):
        async def open(self, **kwargs: Any) -> Any:
            titles.append(kwargs["title"])
            return await super().open(**kwargs)

    runner = OpencodeRunner(OpencodeOptions(command=command, host=Host()))
    result = await runner.run(req(tmp_path, label="#7 review", step_key="r:7:rev"), ctx())
    assert result.ok and titles == ["#7 review"]
    [log] = list((tmp_path / ".whiletrue" / "sessions").glob("*/stream.log"))
    assert "line one" in log.read_text() and "[tool] grep" in log.read_text()
    assert Path(str(log) + ".done").exists()


async def test_a_pane_that_cannot_open_does_not_stop_the_step(tmp_path: Path) -> None:
    command = fake_opencode(tmp_path, [event("text", text="ok")])

    class Broken:
        async def open(self, **kwargs: Any) -> Any:
            raise RuntimeError("no tmux")

    result = await OpencodeRunner(OpencodeOptions(command=command, host=Broken())).run(
        req(tmp_path), ctx()
    )
    assert result.ok


def test_the_plugin_registers_the_runner() -> None:
    registry = Registry()
    registry.use(opencode_plugin())
    runner = registry.get_runner("opencode")
    assert runner is not None and runner.capabilities.fork and not runner.capabilities.budget
