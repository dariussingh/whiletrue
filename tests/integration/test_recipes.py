"""M1 exit: the `ralph` and `review-pass` recipes run end to end, and replay reproduces them."""

import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.cli import EXIT, Io, main

RESULT = (
    '{"type":"result","subtype":"success","is_error":false,"result":"done",%s'
    '"total_cost_usd":0.01,"num_turns":1,"session_id":"s"}'
)

FAKE_CLAUDE = f"""#!/bin/sh
cat > last-prompt.txt
if grep -q REVIEW last-prompt.txt; then
  n=$(cat reviews 2>/dev/null || echo 0); n=$((n+1)); echo $n > reviews
  if [ "$n" -ge "$PASS_AT" ]; then P=true; else P=false; fi
  echo '{RESULT % '"structured_output":{"pass":\'$P\',"reason":"meh"},'}'
elif grep -q FIX last-prompt.txt; then
  echo fixed >> fixes.txt
  echo '{RESULT % ""}'
else
  n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count
  [ "$n" -ge 2 ] && touch done.flag
  echo '{RESULT % ""}'
fi
"""

AGENT = "---\nagent: {name}\n{extra}limits: {{ budget_usd: 1, turns: 5, wall: 1m }}\n---\n{body}\n"


@dataclass
class Cli:
    dir: Path
    out: str = ""
    err: str = ""
    _unused: None = field(default=None, repr=False)

    async def __call__(self, *args: str) -> int:
        def out(text: str) -> None:
            self.out += text

        def err(text: str) -> None:
            self.err += text

        return await main(list(args), Io(cwd=str(self.dir), out=out, err=err, signal=AbortSignal()))

    def reset(self) -> None:
        self.out = self.err = ""

    def write(self, name: str, text: str) -> None:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cli]:
    fake = tmp_path / "fake-claude.sh"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("PASS_AT", "2")
    yield Cli(tmp_path)


def run_id(text: str) -> str:
    match = re.search(r"run ([0-9a-f-]{36}): ", text)
    assert match
    return match.group(1)


async def test_the_ralph_recipe_runs_until_the_gate_passes_and_replays(cli: Cli) -> None:
    cli.write(
        "whiletrue.yaml",
        "name: r\npipeline:\n  - composite:\n      uses: 'recipe:ralph'\n"
        "      params: { agent: ./agents/work.md, gate: 'test -f done.flag', max: 4 }\n",
    )
    cli.write("agents/work.md", AGENT.format(name="work", extra="", body="WORK. {{iter.previous}}"))
    assert await cli("validate") == EXIT.ok
    assert await cli("run") == EXIT.ok
    assert (cli.dir / "count").read_text().strip() == "2"
    recorded = run_id(cli.out)

    (cli.dir / "fake-claude.sh").unlink()
    (cli.dir / "done.flag").unlink()
    cli.reset()
    assert await cli("replay", recorded) == EXIT.ok
    assert "replay matches the recorded run (done)" in cli.out
    assert (cli.dir / "count").read_text().strip() == "2"


REVIEW_PIPELINE = (
    "name: rp\npipeline:\n  - composite:\n      uses: 'recipe:review-pass'\n"
    "      params: { reviewer: ./agents/reviewer.md, fixer: ./agents/fixer.md, max_rounds: 3 }\n"
)


def review_project(cli: Cli) -> None:
    cli.write("whiletrue.yaml", REVIEW_PIPELINE)
    cli.write(
        "agents/reviewer.md",
        AGENT.format(name="reviewer", extra="outputs: { out: Verdict }\n", body="REVIEW the diff"),
    )
    cli.write("agents/fixer.md", AGENT.format(name="fixer", extra="", body="FIX {{input}}"))


async def test_the_review_pass_recipe_fixes_until_approved_and_replays(cli: Cli) -> None:
    review_project(cli)
    assert await cli("validate") == EXIT.ok
    assert await cli("run") == EXIT.ok
    assert (cli.dir / "reviews").read_text().strip() == "2"
    assert (cli.dir / "fixes.txt").read_text().strip() == "fixed"
    recorded = run_id(cli.out)

    (cli.dir / "fake-claude.sh").unlink()
    cli.reset()
    assert await cli("replay", recorded) == EXIT.ok
    assert "replay matches the recorded run (done)" in cli.out
    assert (cli.dir / "reviews").read_text().strip() == "2"


async def test_the_review_pass_recipe_parks_when_rounds_run_out(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PASS_AT", "99")
    review_project(cli)
    assert await cli("run") == EXIT.parked
    assert (cli.dir / "reviews").read_text().strip() == "3"
