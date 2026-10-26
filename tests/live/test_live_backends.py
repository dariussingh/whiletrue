"""Opt-in checks against the real agent CLIs. They spend tokens, so they only run when asked:

    WHILETRUE_LIVE=1 uv run pytest tests/live -v

A backend that is not installed, or not logged in, is reported and skipped, not failed.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main

pytestmark = pytest.mark.skipif(
    not os.environ.get("WHILETRUE_LIVE"), reason="set WHILETRUE_LIVE=1 to run against real agents"
)

PIPELINE = """name: live
pipeline:
  retry:
    max: 3          # models sometimes narrate a tool call instead of making it; the gate catches it
    do:
      - agent: { uses: ./agents/write.md }
      - gate.shell: { run: "grep -q 'hello from {runner}' note.txt" }
"""

AGENT = """---
agent: write
runner: {runner}
permission_mode: acceptEdits
tools: [Read, Write, Edit]
limits: { wall: 4m }
---
Create a file named note.txt in the current directory containing exactly: hello from {runner}
Reply with the single word done.
"""

BACKENDS = [
    ("claude-code-headless", "claude"),
    ("opencode", "opencode"),
    ("codex", "codex"),
]


def project(directory: Path, runner: str) -> None:
    (directory / "agents").mkdir()
    (directory / "agents" / "write.md").write_text(AGENT.replace("{runner}", runner))
    (directory / "whiletrue.yaml").write_text(PIPELINE.replace("{runner}", runner))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=directory, check=True)
    (directory / ".gitignore").write_text(".whiletrue/\n")


def _step_outputs(directory: Path) -> str:
    """What the steps said (an agent's failure message lives there, not on the console)."""
    db = directory / ".whiletrue" / "state.db"
    if not db.exists():
        return ""
    with sqlite3.connect(db) as conn:
        return " ".join(str(row[0]) for row in conn.execute("SELECT output FROM steps"))


@pytest.mark.parametrize(("runner", "binary"), BACKENDS)
async def test_a_real_agent_does_the_work_and_the_gate_agrees(
    runner: str, binary: str, tmp_path: Path
) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} is not installed")
    project(tmp_path, runner)
    out: list[str] = []
    err: list[str] = []
    code = await main(["run"], Io(cwd=str(tmp_path), out=out.append, err=err.append))
    text = "".join(out) + "".join(err) + _step_outputs(tmp_path)
    if code != EXIT.ok and (
        "401" in text
        or "unauthorized" in text.lower()
        or "log in" in text.lower()
        or "login" in text.lower()
    ):
        pytest.skip(f"{binary} is installed but not logged in")
    assert code == EXIT.ok, text
    assert (tmp_path / "note.txt").read_text().strip() == f"hello from {runner}"
