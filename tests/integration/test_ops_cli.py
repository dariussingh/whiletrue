"""Operating runs from the CLI: crash recovery, rerun, cancel, logs, cost, parallel items."""

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.cli import EXIT, Io, main
from whiletrue.cli.ops import RunLock
from whiletrue.store_sqlite import SqliteStore


@dataclass
class Cli:
    dir: Path
    out: str = ""
    err: str = ""

    async def __call__(self, *args: str, signal: AbortSignal | None = None) -> int:
        def out(text: str) -> None:
            self.out += text

        def err(text: str) -> None:
            self.err += text

        return await main(list(args), Io(cwd=str(self.dir), out=out, err=err, signal=signal))

    def reset(self) -> None:
        self.out = self.err = ""


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cli]:
    monkeypatch.delenv("WHILETRUE_CLAUDE_BIN", raising=False)
    yield Cli(tmp_path)


def write(cli: Cli, text: str) -> None:
    (cli.dir / "whiletrue.yaml").write_text(text)


def lines(cli: Cli, name: str = "log.txt") -> list[str]:
    return (cli.dir / name).read_text().split()


GATED = """pipeline:
  - shell: { run: "echo build >> log.txt" }
  - id: review
    human.approve: {}
  - shell: { run: "echo shipped >> log.txt" }
"""


async def test_a_crashed_run_is_recovered_without_redoing_finished_work(cli: Cli) -> None:
    write(cli, GATED)
    assert await cli("run") == EXIT.parked
    # Simulate a crash: the run says "running", a step was in flight, nobody holds the lock.
    store = SqliteStore(cli.dir / ".whiletrue" / "state.db")
    run = store.latest_run()
    assert run is not None
    store._db.execute("UPDATE runs SET status = 'running', ended_at = NULL WHERE id = ?", (run.id,))
    store._db.execute("UPDATE steps SET status = 'running', port = NULL WHERE node_path = 'review'")
    store.close()

    await cli("approve", "main")
    cli.reset()
    assert await cli("resume") == EXIT.ok, cli.err
    assert "interrupted" in cli.out and "recovering" in cli.out
    assert lines(cli) == ["build", "shipped"]  # `build` was not run again


async def test_a_live_run_cannot_be_resumed_or_started_twice(cli: Cli) -> None:
    write(cli, GATED)
    await cli("run")
    lock = RunLock(str(cli.dir))
    assert lock.acquire()
    try:
        assert await cli("run") == EXIT.failed
        assert "Another whiletrue run is active" in cli.err
        assert await cli("resume") == EXIT.failed
        assert await cli("rerun", "main") == EXIT.failed
    finally:
        lock.release()
    assert lock.holder() == str(os.getpid())
    assert await cli("run") == EXIT.parked  # the lock is free again


async def test_rerun_replays_what_came_before_the_step(cli: Cli) -> None:
    write(
        cli,
        "pipeline:\n  - id: one\n    shell: { run: 'echo one >> log.txt' }\n"
        "  - id: two\n    shell: { run: 'echo two >> log.txt' }\n",
    )
    assert await cli("run") == EXIT.ok
    assert await cli("rerun", "main", "--from", "two") == EXIT.ok
    assert lines(cli) == ["one", "two", "two"]  # only step two ran again
    assert await cli("rerun", "main") == EXIT.ok
    assert lines(cli) == ["one", "two", "two", "one", "two"]  # from the start: everything
    cli.reset()
    assert await cli("rerun", "main", "--from", "nope") == EXIT.failed
    assert 'no step "nope"' in cli.err
    assert await cli("rerun", "ghost") == EXIT.failed
    assert await cli("rerun") == EXIT.usage


async def test_rerun_errors_without_history(cli: Cli) -> None:
    write(cli, "pipeline:\n  - shell: { run: 'true' }\n")
    assert await cli("rerun", "main") == EXIT.failed
    assert "No runs yet" in cli.err


async def test_cancel_a_parked_item_closes_its_gate_and_resume_skips_it(cli: Cli) -> None:
    write(cli, GATED)
    await cli("run")
    assert await cli("cancel", "main") == EXIT.ok
    cli.reset()
    await cli("status")
    assert "waiting for a person" not in cli.out
    cli.reset()
    assert await cli("history", "main") == EXIT.ok
    assert "rejected by cancel" in cli.out
    assert await cli("cancel", "main") == EXIT.failed and "already cancelled" in cli.err
    assert await cli("cancel", "ghost") == EXIT.failed
    assert await cli("cancel") == EXIT.usage
    assert await cli("resume") == EXIT.ok  # nothing left to run
    assert lines(cli) == ["build"]


async def test_cancel_needs_a_database(cli: Cli) -> None:
    assert await cli("cancel", "x") == EXIT.failed
    assert await cli("logs", "x") == EXIT.failed
    assert await cli("cost") == EXIT.failed


async def test_logs_show_an_items_events(cli: Cli) -> None:
    write(cli, "pipeline:\n  - shell: { run: 'true' }\n")
    await cli("run")
    cli.reset()
    assert await cli("logs", "main") == EXIT.ok
    assert "step.started" in cli.out and "item.state" in cli.out and "state=done" in cli.out
    cli.reset()
    assert await cli("logs", "ghost") == EXIT.failed and "No events" in cli.err
    assert await cli("logs") == EXIT.usage


async def test_logs_follow_stops_when_the_run_is_over(cli: Cli) -> None:
    write(cli, "pipeline:\n  - shell: { run: 'true' }\n")
    await cli("run")
    cli.reset()
    assert await asyncio.wait_for(cli("logs", "main", "--follow"), 5) == EXIT.ok
    assert "run" not in cli.err


async def test_cost_adds_up_by_item_step_and_runner(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = cli.dir / "fake-claude"
    fake.write_text(
        "#!/bin/sh\ncat > /dev/null\necho "
        '\'{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"total_cost_usd":0.25,"num_turns":1,"session_id":"s"}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(fake))
    (cli.dir / "agents").mkdir()
    (cli.dir / "agents" / "a.md").write_text(
        "---\nagent: a\nlimits: { budget_usd: 1, turns: 3, wall: 1m }\n---\nDo it\n"
    )
    write(cli, "pipeline:\n  - agent: { uses: ./agents/a.md }\n")
    assert await cli("run") == EXIT.ok
    cli.reset()
    assert await cli("cost") == EXIT.ok
    assert "total $0.2500" in cli.out
    assert "by item" in cli.out and "main" in cli.out
    assert "claude-code-headless" in cli.out and "$0.2500" in cli.out
    cli.reset()
    assert await cli("cost", "nope") == EXIT.failed


async def test_items_run_in_parallel_from_a_json_source(cli: Cli) -> None:
    (cli.dir / "items.json").write_text(json.dumps([{"id": str(i)} for i in range(4)]))
    write(
        cli,
        "parallel: 4\nsource: { json: { path: items.json } }\n"
        "pipeline:\n  - shell: { run: 'sleep 0.3; echo {{item.id}} >> log.txt' }\n",
    )
    started = asyncio.get_running_loop().time()
    assert await cli("run") == EXIT.ok
    took = asyncio.get_running_loop().time() - started
    assert sorted(lines(cli)) == ["0", "1", "2", "3"]
    assert took < 1.0  # four 0.3s items overlapped (sequentially they would take 1.2s)


async def test_a_dependency_cycle_is_rejected_before_anything_runs(cli: Cli) -> None:
    (cli.dir / "items.json").write_text(
        json.dumps([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}])
    )
    write(cli, "source: { json: { path: items.json } }\npipeline:\n  - shell: { run: 'true' }\n")
    assert await cli("run") == EXIT.failed
    assert "cycle" in cli.err and "a -> b" in cli.err


async def test_status_explains_why_an_item_is_blocked(cli: Cli) -> None:
    (cli.dir / "items.json").write_text(json.dumps([{"id": "a"}, {"id": "b", "depends_on": ["a"]}]))
    write(
        cli,
        "source: { json: { path: items.json } }\npipeline:\n  - emit_fail: {}\n".replace(
            "emit_fail: {}", "shell: { run: 'false' }"
        ),
    )
    assert await cli("run") == EXIT.parked
    cli.reset()
    await cli("status")
    assert "b  pending" in cli.out and "blocked by a" in cli.out


async def test_plan_lists_items_and_rejects_cycles(cli: Cli) -> None:
    (cli.dir / "TASKS.md").write_text("- [ ] A {#a}\n- [ ] B {#b after=a}\n")
    write(
        cli,
        "parallel: 2\nsource: { markdown_tasks: { path: TASKS.md } }\n"
        "pipeline:\n  - shell: { run: 'true' }\n",
    )
    assert await cli("plan") == EXIT.ok
    assert "items (2, up to 2 at once)" in cli.out and "b  after a" in cli.out
    (cli.dir / "TASKS.md").write_text("- [ ] A {#a after=b}\n- [ ] B {#b after=a}\n")
    cli.reset()
    assert await cli("plan") == EXIT.failed
    assert "a -> b" in cli.err
    (cli.dir / "TASKS.md").unlink()
    cli.reset()
    assert await cli("plan") == EXIT.failed
    assert "Cannot fetch items" in cli.err


async def test_resume_takes_the_sources_latest_word(cli: Cli) -> None:
    (cli.dir / "TASKS.md").write_text("- [ ] A {#a}\n- [ ] B {#b}\n")
    write(
        cli,
        "source: { markdown_tasks: { path: TASKS.md } }\npipeline:\n"
        "  - shell: { run: 'echo {{item.id}} >> log.txt' }\n"
        "  - human.approve: {}\n"
        "  - shell: { run: 'echo {{item.id}}-after >> log.txt' }\n",
    )
    assert await cli("run") == EXIT.parked
    await cli("approve", "a")
    await cli("approve", "b")
    # While the run was parked: A was ticked off in the file, C was added.
    (cli.dir / "TASKS.md").write_text("- [x] A {#a}\n- [ ] B {#b}\n- [ ] C {#c}\n")
    cli.reset()
    assert await cli("resume") == EXIT.parked  # C is new and waits at its own gate
    assert "no longer in the source, dropped: a" in cli.out
    assert "c-after" not in lines(cli) and "b-after" in lines(cli) and "a-after" not in lines(cli)


async def test_cost_adds_up_across_a_run_and_its_resumes(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = cli.dir / "fake-claude"
    fake.write_text(
        "#!/bin/sh\ncat > /dev/null\necho "
        '\'{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"total_cost_usd":0.5,"num_turns":1,"session_id":"s"}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(fake))
    (cli.dir / "agents").mkdir()
    (cli.dir / "agents" / "a.md").write_text(
        "---\nagent: a\nlimits: { budget_usd: 1, turns: 3, wall: 1m }\n---\nDo it\n"
    )
    write(
        cli,
        "pipeline:\n  - agent: { uses: ./agents/a.md }\n  - human.approve: {}\n"
        "  - agent: { uses: ./agents/a.md }\n",
    )
    assert await cli("run") == EXIT.parked
    await cli("approve", "main")
    assert await cli("resume") == EXIT.ok
    cli.reset()
    assert await cli("cost") == EXIT.ok
    assert "total $1.0000 (over 2 runs)" in cli.out  # 0.50 before the gate + 0.50 after


async def test_a_failed_item_says_why_in_the_run_summary(cli: Cli) -> None:
    write(cli, "pipeline:\n  - shell: { run: 'echo nope >&2; exit 3' }\n")
    assert await cli("run") == EXIT.parked
    assert "main: parked (fail) — `echo nope >&2; exit 3` exited with code 3" in cli.out


async def test_a_park_for_a_person_says_what_it_is_waiting_on(cli: Cli) -> None:
    write(cli, "pipeline:\n  - human.approve: { what: the plan }\n")
    assert await cli("run") == EXIT.parked
    assert "main: parked (needs_human)" in cli.out
