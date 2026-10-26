"""The v0.1 command surface: every command exists, is in --help, and is documented."""

import re
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main
from whiletrue.cli.app import USAGE

ROOT = Path(__file__).resolve().parents[2]

#: Every command in the v0.1 surface (docs/todo.md item 73), plus the ones added since.
COMMANDS = [
    "init", "validate", "plan", "run", "resume", "status", "attach", "approve", "reject",
    "comment", "history", "rerun", "cancel", "logs", "cost", "replay",
    "lock", "learnings", "panel",
]  # fmt: skip


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_is_in_the_help_text(command: str) -> None:
    assert re.search(rf"^  {command}\b", USAGE, re.MULTILINE), f"{command} missing from --help"


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_is_documented_in_the_cli_reference(command: str) -> None:
    text = (ROOT / "docs" / "cli.md").read_text()
    assert f"`whiletrue {command}" in text, f"{command} missing from docs/cli.md"


@pytest.mark.parametrize("command", [c for c in COMMANDS if c not in ("panel", "attach")])
async def test_every_command_is_dispatched(command: str, tmp_path: Path) -> None:
    out: list[str] = []
    err: list[str] = []
    code = await main([command], Io(cwd=str(tmp_path), out=out.append, err=err.append))
    assert "Unknown command" not in "".join(err), command
    assert code in (EXIT.ok, EXIT.failed, EXIT.usage, EXIT.parked)


async def test_unknown_commands_are_a_usage_error(tmp_path: Path) -> None:
    err: list[str] = []
    code = await main(["frobnicate"], Io(cwd=str(tmp_path), out=lambda _: None, err=err.append))
    assert code == EXIT.usage and 'Unknown command "frobnicate"' in "".join(err)


def test_every_exit_code_is_documented() -> None:
    text = (ROOT / "docs" / "cli.md").read_text()
    for code in ("0", "1", "2", "64", "130"):
        assert re.search(rf"^\| {code} \|", text, re.MULTILINE), f"exit code {code} undocumented"
