"""M3 exit: 4 tasks run in parallel in separate git worktrees; killing the process mid-run and
resuming completes the work without repeating any effect.

A real `whiletrue run` subprocess is killed with SIGKILL, so this exercises the run lock, SQLite
after a hard kill, crash recovery, replay of finished steps, and worktree reuse.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main

PIPELINE = """name: m3
parallel: 4
source: { json: { path: tasks.json } }
workspace: { git-worktree: { branch: "wt/{{item.id}}" } }
pipeline:
  - id: start
    shell: { run: "echo start-{{item.id}} >> $ROOT/effects.log; echo {{item.id}} > work.txt" }
  - id: slow
    shell: { run: "[ -e $ROOT/proceed.flag ] || sleep 60" }
  - id: finish
    shell: { run: "echo finish-{{item.id}} >> $ROOT/effects.log" }
  - git.commit: { message: "task {{item.id}}" }
"""


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("project\n")
    (repo / ".gitignore").write_text(".whiletrue/\neffects.log\nproceed.flag\n")
    (repo / "tasks.json").write_text(json.dumps([{"id": str(i)} for i in range(4)]))
    (repo / "whiletrue.yaml").write_text(PIPELINE)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


def effects(repo: Path) -> list[str]:
    path = repo / "effects.log"
    return sorted(path.read_text().split()) if path.exists() else []


async def test_kill_mid_run_then_resume_repeats_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = project.parent / "first-run.log"
    env = {**os.environ, "ROOT": str(project)}
    monkeypatch.setenv("ROOT", str(project))
    process = subprocess.Popen(  # noqa: ASYNC220
        [sys.executable, "-m", "whiletrue", "run"],
        cwd=project,
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 90
        while effects(project) != [f"start-{i}" for i in range(4)]:
            assert process.poll() is None, f"whiletrue exited early:\n{log.read_text()}"
            assert time.time() < deadline, f"the four tasks never started:\n{log.read_text()}"
            await asyncio.sleep(0.1)
        # All four are now in the slow step, in four separate worktrees.
        trees = sorted(p.name for p in (project / ".whiletrue" / "worktrees").iterdir())
        assert trees == ["0", "1", "2", "3"]
        os.killpg(process.pid, signal.SIGKILL)
    finally:
        process.wait(timeout=10)
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    assert process.returncode == -signal.SIGKILL

    (project / "proceed.flag").write_text("go")
    out: list[str] = []
    err: list[str] = []
    code = await main(["resume"], Io(cwd=str(project), out=out.append, err=err.append))
    assert code == EXIT.ok, ("".join(out), "".join(err))
    assert "was interrupted" in "".join(out)

    # Each effect happened exactly once: `start` was replayed, not repeated.
    assert effects(project) == sorted(
        [f"start-{i}" for i in range(4)] + [f"finish-{i}" for i in range(4)]
    )
    # Every task committed in its own worktree on its own branch; the main branch is untouched.
    for i in range(4):
        assert git(project, "log", "--format=%s", f"wt/{i}", "-n", "1").strip() == f"task {i}"
        assert git(project, "show", f"wt/{i}:work.txt").strip() == str(i)
    assert git(project, "log", "--format=%s", "main").split() == ["base"]
    # Finished items give their worktrees back.
    assert not any((project / ".whiletrue" / "worktrees").iterdir())
