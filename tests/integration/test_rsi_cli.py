"""Recursive self-improvement end to end through the CLI, on a fake headless claude."""

import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main

FAKE = Path(__file__).resolve().parent.parent / "fixtures" / "fake_rsi_claude.py"

AGENT = """---
agent: dev
runner: claude-code-headless
permission_mode: acceptEdits
limits: { budget_usd: 1, turns: 10, wall: 5m }
---
Implement the task.
"""


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
    launcher = tmp_path / "claude"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(launcher))
    project = tmp_path / "proj"
    (project / "agents").mkdir(parents=True)
    (project / "agents" / "dev.md").write_text(AGENT)
    (project / "CLAUDE.md").write_text("Base rules.\n")
    yield Cli(project)


def write(cli: Cli, rsi: str) -> None:
    (cli.dir / "whiletrue.yaml").write_text(
        f"name: demo\nrsi: {rsi}\npipeline:\n  - agent: {{ uses: ./agents/dev.md }}\n"
    )


async def test_rsi_off_is_byte_for_byte_a_normal_run(cli: Cli) -> None:
    write(cli, "false")
    assert await cli("plan") == EXIT.ok
    assert "rsi" not in cli.out.replace("rsi: false", "")
    cli.reset()
    assert await cli("run") == EXIT.ok
    assert (cli.dir / "out.txt").read_text() == "plain"
    assert not (cli.dir / ".whiletrue" / "rsi").exists()


async def test_plan_shows_the_expanded_rsi_pipeline_and_warns_about_cost(cli: Cli) -> None:
    write(cli, "true")
    assert await cli("plan") == EXIT.ok
    for step in ("rsi.harness2", "rsi.review", "human.approve", "rsi.adopt", "rsi.discard"):
        assert step in cli.out
    assert "7x one agent run" in cli.err and "sets no budget" in cli.err
    assert await cli("validate") == EXIT.ok


async def test_a_bad_setting_stops_with_a_clear_message(cli: Cli) -> None:
    write(cli, "{ scope: [nope] }")
    assert await cli("validate") == EXIT.failed
    assert "unknown top-level step" in cli.err


async def test_run_improve_review_adopt(cli: Cli) -> None:
    write(cli, "{ k: 1, budget_usd: 5 }")
    # the run improves the harness, applies the winner, and parks at the review gate
    assert await cli("run") == EXIT.parked
    assert (cli.dir / "out.txt").read_text() == "careful"
    assert (cli.dir / "CLAUDE.md").read_text() == "Base rules.\n"
    cli.reset()
    assert await cli("rsi", "status") == EXIT.ok
    assert "main  pending  agg1  changed  4 rollouts, 3 sessions" in cli.out
    cli.reset()
    assert await cli("rsi", "diff", "main") == EXIT.ok
    assert "+BE CAREFUL with config files." in cli.out
    # the person approves at the gate and resumes: the harness is adopted
    cli.reset()
    assert await cli("approve", "main") == EXIT.ok
    assert await cli("resume") == EXIT.ok
    assert "BE CAREFUL" in (cli.dir / "CLAUDE.md").read_text()
    cli.reset()
    assert await cli("rsi", "status") == EXIT.ok and "adopted" in cli.out
    cli.reset()
    assert await cli("rsi", "library") == EXIT.ok
    assert "proj  (1 entries)" in cli.out and "main" in cli.out


async def test_reject_discards_the_harness_but_keeps_the_work(cli: Cli) -> None:
    write(cli, "{ k: 1, budget_usd: 5 }")
    assert await cli("run") == EXIT.parked
    assert await cli("reject", "main", "--note", "too aggressive") == EXIT.ok
    assert await cli("resume") == EXIT.ok
    assert (cli.dir / "CLAUDE.md").read_text() == "Base rules.\n"
    assert (cli.dir / "out.txt").read_text() == "careful"
    cli.reset()
    await cli("rsi", "status")
    assert "discarded" in cli.out


async def test_adopt_by_hand_and_reset(cli: Cli) -> None:
    write(cli, "{ k: 1, budget_usd: 5, adopt: never }")
    assert await cli("run") == EXIT.ok
    assert await cli("rsi", "adopt", "main") == EXIT.ok
    assert "BE CAREFUL" in (cli.dir / "CLAUDE.md").read_text()
    cli.reset()
    assert await cli("rsi", "adopt", "main") == EXIT.failed and "is already adopted" in cli.err
    cli.reset()
    assert await cli("rsi", "reset", "main") == EXIT.ok
    assert await cli("rsi", "reset", "main") == EXIT.failed
    assert await cli("rsi", "status") == EXIT.ok and "No rsi proposals" in cli.out


async def test_adopting_executable_files_needs_a_flag(cli: Cli) -> None:
    write(cli, "{ k: 1, budget_usd: 5, adopt: never }")
    assert await cli("run") == EXIT.ok
    proposal = cli.dir / ".whiletrue/rsi/items/main/proposal.json"
    data = json.loads(proposal.read_text())
    data["restricted"] = [".claude/scripts/x.py"]
    data["chosen"][".claude/scripts/x.py"] = "print(1)\n"
    proposal.write_text(json.dumps(data))
    cli.reset()
    assert await cli("rsi", "diff", "main") == EXIT.ok and "execute code" in cli.out
    cli.reset()
    assert await cli("rsi", "adopt", "main") == EXIT.failed
    assert "--yes" in cli.err and not (cli.dir / ".claude/scripts/x.py").exists()
    assert await cli("rsi", "adopt", "main", "--yes") == EXIT.ok
    assert (cli.dir / ".claude/scripts/x.py").read_text() == "print(1)\n"


async def test_rsi_commands_handle_missing_input(cli: Cli) -> None:
    assert await cli("rsi") == EXIT.usage and "Usage: whiletrue rsi" in cli.err
    cli.reset()
    assert await cli("rsi", "status") == EXIT.ok and "No rsi proposals" in cli.out
    assert await cli("rsi", "library") == EXIT.ok and "library is empty" in cli.out
    for sub in ("diff", "adopt", "reset"):
        cli.reset()
        assert await cli("rsi", sub) == EXIT.usage
    cli.reset()
    assert (
        await cli("rsi", "diff", "nope") == EXIT.failed and 'No rsi proposal for "nope"' in cli.err
    )
    assert await cli("rsi", "adopt", "nope") == EXIT.failed


async def test_a_model_can_approve_instead_of_a_person(cli: Cli) -> None:
    write(cli, "{ k: 1, budget_usd: 5, adopt: model }")
    assert await cli("run") == EXIT.ok  # no human gate
    assert "BE CAREFUL" in (cli.dir / "CLAUDE.md").read_text()
    cli.reset()
    assert await cli("rsi", "status") == EXIT.ok
    assert "adopted" in cli.out and "model: approve" in cli.out
