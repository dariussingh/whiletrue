"""Opt-in: recursive self-improvement against real Claude Code (about one dollar).

    WHILETRUE_LIVE=1 uv run pytest tests/live/test_live_rsi.py -v

One tiny item, k=1, adopt: never, hard-capped at $4. It checks the machinery (4 rollouts, 3
sessions, the bug fixed, a proposal recorded), not that the harness got better.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main

pytestmark = pytest.mark.skipif(
    not os.environ.get("WHILETRUE_LIVE"), reason="set WHILETRUE_LIVE=1 to run against real agents"
)

CALC = "def average(values):\n    return sum(values) / len(values) + 1\n"
TESTS = "from calc import average\n\n\ndef test_average():\n    assert average([2, 4, 6]) == 4\n"
AGENT = """---
agent: dev
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read, Edit, Write, Glob, Grep, "Bash(pytest*)"]
limits: { budget_usd: 0.6, turns: 15, wall: 6m }
---
Task: {{item.title}}.
Make `pytest -q` pass by fixing the code, not the tests. Reply with one sentence.
"""
PIPELINE = """name: live-rsi
source: { markdown_tasks: { path: TASKS.md } }
rsi:
  k: 1
  budget_usd: 4
  adopt: never
  improver:
    limits: { budget_usd: 0.6, turns: 20, wall: 8m }
pipeline:
  - agent: { uses: ./agents/dev.md }
  - gate.tests: {}
"""


async def test_a_real_agent_improves_its_harness_for_one_item(tmp_path: Path) -> None:
    if shutil.which("claude") is None:
        pytest.skip("claude is not installed")
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "dev.md").write_text(AGENT)
    (tmp_path / "calc.py").write_text(CALC)
    (tmp_path / "test_calc.py").write_text(TESTS)
    (tmp_path / "TASKS.md").write_text("- [ ] Fix the failing test in calc.py {#fix}\n")
    (tmp_path / "CLAUDE.md").write_text("Python project. Keep changes small.\n")
    (tmp_path / "whiletrue.yaml").write_text(PIPELINE)
    (tmp_path / ".gitignore").write_text(".whiletrue/\n__pycache__/\n")
    out: list[str] = []
    err: list[str] = []
    code = await main(["run"], Io(cwd=str(tmp_path), out=out.append, err=err.append))
    text = "".join(out) + "".join(err)
    if code != EXIT.ok and ("401" in text or "log in" in text.lower()):
        pytest.skip("claude is installed but not logged in")
    assert code == EXIT.ok, text
    assert "+ 1" not in (tmp_path / "calc.py").read_text()
    proposal = json.loads((tmp_path / ".whiletrue/rsi/items/fix/proposal.json").read_text())
    assert (proposal["ledger"]["rollouts"], proposal["ledger"]["sessions"]) == (4, 3)
    assert proposal["ledger"]["spent"] < 4
