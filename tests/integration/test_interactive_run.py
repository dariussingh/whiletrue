"""M2 exit: a step completes in a live pane and its typed result is submitted.

The agent is a stand-in `claude` that behaves like the real plugin (hooks + `whiletrue_submit`),
so the whole path runs for real: CLI -> runtime -> agent module -> interactive runner -> endpoint
-> pane (a real tmux server when tmux is installed) -> result -> gate.
"""

import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main
from whiletrue.store_sqlite import SqliteStore
from whiletrue.tmux import ProcessPaneHost

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_interactive_claude.py"

PIPELINE = """name: interactive
pipeline:
  - agent: { uses: ./agents/impl.md }
  - gate.shell: { run: "test -f done.flag" }
"""

AGENT = """---
agent: impl
runner: claude-code
permission_mode: acceptEdits
limits: { budget_usd: 1, turns: 10, wall: 2m }
---
Implement the thing for {{item.id}}.
"""


def project(directory: Path, monkeypatch: pytest.MonkeyPatch, mode: str = "submit") -> Path:
    (directory / "agents").mkdir()
    (directory / "whiletrue.yaml").write_text(PIPELINE)
    (directory / "agents" / "impl.md").write_text(AGENT)
    fake = directory / "fake-claude"
    fake.write_text(FIXTURE.read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("FAKE_OUT", str(directory / "out"))
    monkeypatch.setenv("FAKE_TOUCH", str(directory / "done.flag"))
    monkeypatch.setenv("FAKE_MODE", mode)
    return directory


async def run_cli(directory: Path, *args: str, host: object | None = None) -> tuple[int, str, str]:
    out: list[str] = []
    err: list[str] = []
    io = Io(cwd=str(directory), out=out.append, err=err.append, pane_host=host)  # type: ignore[arg-type]
    code = await main(list(args), io)
    return code, "".join(out), "".join(err)


async def test_a_step_runs_in_a_pane_and_its_result_is_submitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path, monkeypatch)
    code, out, err = await run_cli(tmp_path, "run", host=ProcessPaneHost())
    assert code == EXIT.ok, (out, err)
    assert "done" in out
    store = SqliteStore(tmp_path / ".whiletrue" / "state.db")
    try:
        run = store.latest_run()
        assert run is not None and run.status == "done"
        steps = store.list_steps(run.id)
        agent_step = next(s for s in steps if s.node_path != "root" and s.output == "42")
        assert agent_step.port == "ok" and agent_step.status == "completed"
        assert agent_step.output == "42"
    finally:
        store.close()
    assert (tmp_path / "done.flag").exists()


async def test_an_agent_that_asks_a_question_parks_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path, monkeypatch, mode="ask")
    code, out, _ = await run_cli(tmp_path, "run", host=ProcessPaneHost())
    assert code == EXIT.parked
    assert "parked" in out


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
async def test_the_same_run_in_a_real_tmux_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket = f"wt-e2e-{uuid.uuid4().hex[:8]}"
    wrapper = tmp_path / "tmux-wrapper"
    wrapper.write_text(f'#!/bin/sh\nexec {shutil.which("tmux")} -L {socket} "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    project(tmp_path, monkeypatch)
    monkeypatch.setenv("WHILETRUE_TMUX_BIN", str(wrapper))
    try:
        code, out, err = await run_cli(tmp_path, "run")
        assert code == EXIT.ok, (out, err)
        assert (tmp_path / "done.flag").exists()
    finally:
        subprocess.run(  # noqa: ASYNC221
            [str(wrapper), "kill-server"], check=False, capture_output=True
        )
