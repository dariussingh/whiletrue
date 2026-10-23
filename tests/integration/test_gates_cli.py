"""Human gates end to end through the CLI: park, decide, resume."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main
from whiletrue.store_sqlite import SqliteStore


@dataclass
class Cli:
    dir: Path
    out: str = ""
    err: str = ""

    async def __call__(self, *args: str) -> int:
        def out(text: str) -> None:
            self.out += text

        def err(text: str) -> None:
            self.err += text

        return await main(list(args), Io(cwd=str(self.dir), out=out, err=err))

    def reset(self) -> None:
        self.out = self.err = ""


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cli]:
    monkeypatch.delenv("WHILETRUE_CLAUDE_BIN", raising=False)
    yield Cli(tmp_path)


PIPELINE = """name: gated
pipeline:
  - shell: { run: "echo build >> log.txt" }
  - id: review
    human.approve: { what: pr }
    wires: { rejected: fix }
  - shell: { run: "echo shipped >> log.txt" }
  - id: fix
    shell: { run: "echo fixed >> log.txt" }
"""


def write(directory: Path, text: str = PIPELINE) -> None:
    (directory / "whiletrue.yaml").write_text(text)


def log(directory: Path) -> list[str]:
    return (directory / "log.txt").read_text().split()


async def test_park_approve_resume_completes_without_redoing_work(cli: Cli) -> None:
    write(cli.dir)
    assert await cli("run") == EXIT.parked
    assert log(cli.dir) == ["build"]

    assert await cli("status") == EXIT.ok
    assert "waiting for a person" in cli.out and "pr at review" in cli.out

    cli.reset()
    assert await cli("approve", "main", "--note", "looks good") == EXIT.ok
    assert "main: approved" in cli.out

    cli.reset()
    assert await cli("resume") == EXIT.ok
    assert "resumed" in cli.out and "done" in cli.out
    # `build` was replayed from the first run; only the steps after the gate ran for real.
    assert log(cli.dir) == ["build", "shipped"]


async def test_reject_routes_to_the_fix_step_with_the_note(cli: Cli) -> None:
    write(cli.dir)
    await cli("run")
    cli.reset()
    assert await cli("reject", "main") == EXIT.usage
    assert "needs a reason" in cli.err

    cli.reset()
    assert await cli("reject", "main", "--note", "add tests") == EXIT.ok
    assert await cli("resume") == EXIT.ok
    assert log(cli.dir) == ["build", "fixed"]

    store = SqliteStore(cli.dir / ".whiletrue" / "state.db")
    try:
        [park] = store.list_parks("main")
        assert (park.decision, park.notes) == ("rejected", "add tests")
        run = store.latest_run()
        assert run is not None
        gate = next(s for s in store.list_steps(run.id) if s.node_path == "review")
        assert gate.port == "rejected" and gate.output["rejection"]["note"] == "add tests"
    finally:
        store.close()


async def test_a_gate_decided_twice_or_never_waiting_is_an_error(cli: Cli) -> None:
    write(cli.dir)
    await cli("run")
    assert await cli("approve", "main") == EXIT.ok
    cli.reset()
    assert await cli("approve", "main") == EXIT.failed
    assert "Nothing is waiting" in cli.err


async def test_decisions_need_an_item_a_database_and_one_gate(cli: Cli) -> None:
    assert await cli("approve") == EXIT.usage
    assert await cli("approve", "main") == EXIT.failed and "No runs yet" in cli.err
    assert await cli("comment", "main", "hi") == EXIT.failed
    assert await cli("history", "main") == EXIT.failed
    assert await cli("resume") == EXIT.failed

    two_gates = PIPELINE.replace(
        '  - shell: { run: "echo shipped >> log.txt" }',
        "  - id: second\n    human.approve: { what: merge }",
    )
    write(cli.dir, two_gates.replace("    wires: { rejected: fix }\n", ""))
    await cli("run")
    # The first gate parks the item; a second gate is only reached after it is approved.
    await cli("approve", "main")
    await cli("resume")
    cli.reset()
    assert await cli("approve", "main", "--step", "nope") == EXIT.failed
    assert await cli("approve", "main", "--step", "second") == EXIT.ok


async def test_resume_needs_a_parked_run(cli: Cli) -> None:
    write(cli.dir, "pipeline:\n  - shell: { run: 'true' }\n")
    assert await cli("run") == EXIT.ok
    cli.reset()
    assert await cli("resume") == EXIT.failed
    assert "not parked" in cli.err
    cli.reset()
    assert await cli("resume", "nope") == EXIT.failed
    assert "No run" in cli.err


async def test_comments_and_history_form_a_timeline(cli: Cli) -> None:
    write(cli.dir)
    await cli("run")
    assert await cli("comment", "main") == EXIT.usage
    assert await cli("comment", "main", "why", "a", "shell", "step?") == EXIT.ok
    await cli("approve", "main", "--note", "fine")
    cli.reset()
    assert await cli("history", "main") == EXIT.ok
    lines = cli.out.splitlines()
    assert any("parked at review: waiting for pr" in line for line in lines)
    assert any("comment from" in line and "why a shell step?" in line for line in lines)
    assert any("approved by" in line and "fine" in line for line in lines)
    cli.reset()
    assert await cli("history") == EXIT.usage
    assert await cli("history", "ghost") == EXIT.failed


async def test_items_come_from_a_source_and_wait_for_their_dependencies(cli: Cli) -> None:
    (cli.dir / "items.json").write_text(json.dumps([{"id": "b", "depends_on": ["a"]}, {"id": "a"}]))
    write(
        cli.dir,
        "source: { json: { path: items.json } }\n"
        "pipeline:\n  - shell: { run: 'echo {{item.id}} >> log.txt' }\n",
    )
    assert await cli("run") == EXIT.ok
    assert log(cli.dir) == ["a", "b"]
    assert await cli("validate") == EXIT.ok


async def test_a_broken_source_is_reported_before_running(cli: Cli) -> None:
    write(
        cli.dir,
        "source: { json: { path: missing.json } }\npipeline:\n  - shell: { run: 'true' }\n",
    )
    assert await cli("run") == EXIT.failed
    assert "Cannot fetch items" in cli.err


async def test_option_parsing_for_notes_and_steps(cli: Cli) -> None:
    assert await cli("approve", "x", "--note") == EXIT.usage
    assert await cli("approve", "x", "--note=hello", "--step=gate") == EXIT.failed
    assert await cli("approve", "x", "-m", "hello") == EXIT.failed


async def test_learnings_are_reviewed_before_they_count(cli: Cli) -> None:
    write(cli.dir, PIPELINE + "  - history.distill: {}\n")
    await cli("run")
    await cli("reject", "main", "--note", "add tests")
    await cli("resume")
    cli.reset()
    assert await cli("learnings") == EXIT.ok
    assert "proposed" in cli.out and "add tests" in cli.out and "from rejection on main" in cli.out
    cli.reset()
    assert await cli("learnings", "approve", "1") == EXIT.ok
    assert await cli("learnings", "reject", "99") == EXIT.failed
    assert await cli("learnings", "approve", "x") == EXIT.usage
    assert await cli("learnings", "bogus") == EXIT.usage
    cli.reset()
    await cli("learnings", "list")
    assert "approved" in cli.out


async def test_no_learnings_yet(cli: Cli) -> None:
    write(cli.dir, "pipeline:\n  - shell: { run: 'true' }\n")
    await cli("run")
    cli.reset()
    assert await cli("learnings") == EXIT.ok
    assert "No learnings yet" in cli.out
