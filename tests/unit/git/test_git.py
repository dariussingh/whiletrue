import subprocess
from pathlib import Path
from typing import Any

import pytest

from whiletrue.basics import basics_plugin
from whiletrue.control import control_plugin
from whiletrue.core import (
    MemoryStore,
    Registry,
    RunItem,
    WorkspaceError,
    parse_pipeline,
    resolve_workspace,
    run_pipeline,
    workspace_spec,
)
from whiletrue.git import DirCopy, GitWorktree, NoWorkspace, git_plugin


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
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("base\n")
    (path / ".gitignore").write_text(".whiletrue/\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    return path


def registry() -> Registry:
    r = Registry()
    r.use(control_plugin)
    r.use(basics_plugin)
    r.use(git_plugin)
    return r


async def run(text: str, cwd: Path, items: list[RunItem] | None = None, **kw: Any):
    return await run_pipeline(
        registry=registry(),
        config=parse_pipeline(text, "t.yaml"),
        store=MemoryStore(),
        cwd=str(cwd),
        items=items,
        **kw,
    )


# ---- workspaces -----------------------------------------------------------------------------


async def test_a_worktree_per_item_on_its_own_branch(repo: Path) -> None:
    text = (
        "workspace: { git-worktree: { branch: 'wt/{{item.id}}' } }\n"
        "pipeline:\n  - shell: { run: 'git branch --show-current > branch.txt && pwd > where.txt' }\n"
    )
    result = await run(text, repo, [RunItem("1"), RunItem("2")], parallel=2)
    assert result.status == "done"
    for item in ("1", "2"):
        tree = repo / ".whiletrue" / "worktrees"
        # `done` items have their worktree removed, but the branch they committed on stays.
        assert not (tree / item).exists()
        assert f"wt/{item}" in git(repo, "branch", "--list", f"wt/{item}")


async def test_a_parked_items_worktree_survives_and_is_reused(repo: Path) -> None:
    park = (
        "workspace: { git-worktree: { branch: 'wt/x' } }\n"
        "pipeline:\n  - shell: { run: 'echo work >> notes.txt' }\n  - human.approve: {}\n"
    )
    first = await run(park, repo, [RunItem("x")])
    assert first.status == "parked"
    tree = repo / ".whiletrue" / "worktrees" / "x"
    assert (tree / "notes.txt").read_text() == "work\n"
    # The same workspace comes back for the next run of the item.
    ws = await GitWorktree().create({"branch": "wt/x"}, {"id": "x"}, str(repo))
    assert ws.path == str(tree)


async def test_worktrees_start_from_the_configured_base(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "other")
    (repo / "other.txt").write_text("o\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "other")
    git(repo, "checkout", "-q", "main")
    ws = await GitWorktree().create({"base": "other", "branch": "wt/b"}, {"id": "b"}, str(repo))
    assert (Path(ws.path) / "other.txt").exists()
    await ws.release("parked")
    assert Path(ws.path).exists()
    await ws.release("done")
    assert not Path(ws.path).exists()


async def test_a_workspace_that_cannot_be_created_fails_the_item(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = await run(
        "workspace: { git-worktree: {} }\npipeline:\n  - shell: { run: 'true' }\n", not_a_repo
    )
    assert result.items[0].port == "fail" and "workspace:" in result.items[0].value["error"]


async def test_dir_copy_gives_each_item_its_own_copy(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "seed.txt").write_text("seed\n")
    text = "workspace: { dir-copy: {} }\npipeline:\n  - shell: { run: 'echo mine >> seed.txt' }\n"
    result = await run(text, project, [RunItem("a"), RunItem("b")])
    assert result.status == "done"
    assert (project / "seed.txt").read_text() == "seed\n"  # the project itself was untouched
    ws = await DirCopy().create({"skip_git": True}, {"id": "c"}, str(project))
    assert (Path(ws.path) / "seed.txt").exists()
    await ws.release("parked")
    assert Path(ws.path).exists()
    await ws.release("done")
    assert not Path(ws.path).exists()


async def test_no_workspace_shares_the_project(repo: Path) -> None:
    ws = await NoWorkspace().create({}, {"id": "x"}, str(repo))
    assert ws.path == str(repo)
    await ws.release("done")


def test_workspace_specs() -> None:
    assert workspace_spec({}) is None
    assert workspace_spec({"workspace": "none"}) == ("none", {})
    assert workspace_spec({"workspace": {"git-worktree": {"base": "x"}}}) == (
        "git-worktree",
        {"base": "x"},
    )
    with pytest.raises(WorkspaceError, match="one provider"):
        workspace_spec({"workspace": {"a": {}, "b": {}}})
    with pytest.raises(WorkspaceError, match="must be a mapping"):
        workspace_spec({"workspace": {"a": 3}})
    assert resolve_workspace(registry(), {}) is None
    with pytest.raises(WorkspaceError, match="Unknown workspace"):
        resolve_workspace(registry(), {"workspace": "nope"})


# ---- modules --------------------------------------------------------------------------------


async def test_commit_stages_and_commits_once(repo: Path) -> None:
    text = (
        "pipeline:\n"
        "  - shell: { run: 'echo new > b.txt' }\n"
        "  - git.commit: { message: add b }\n"
        "  - git.commit: { message: nothing to do }\n"
    )
    result = await run(text, repo)
    assert result.status == "done"
    assert result.items[0].value["committed"] is False  # the second commit had nothing to commit
    assert git(repo, "log", "--format=%s", "-n", "2").splitlines() == ["add b", "base"]


async def test_commit_reports_whether_it_committed(repo: Path) -> None:
    (repo / "c.txt").write_text("c\n")
    first = await run("pipeline:\n  - git.commit: { message: c }\n", repo)
    assert first.items[0].value["committed"] is True and len(first.items[0].value["sha"]) == 40
    empty = await run("pipeline:\n  - git.commit: { message: e, allow_empty: true }\n", repo)
    assert empty.items[0].value["committed"] is True


async def test_commit_fails_cleanly_outside_a_repo(tmp_path: Path) -> None:
    result = await run("pipeline:\n  - git.commit: { message: x }\n", tmp_path)
    assert result.items[0].port == "fail" and "git add failed" in result.items[0].value["error"]


async def test_commit_push_pushes_the_branch(tmp_path: Path, repo: Path) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(  # noqa: ASYNC221
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-u", "origin", "main")
    (repo / "d.txt").write_text("d\n")
    result = await run("pipeline:\n  - git.commit_push: { message: d }\n", repo)
    assert result.items[0].value["pushed"] is True
    assert "d" in git(tmp_path, "--git-dir", str(origin), "log", "--format=%s", "main")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    (repo / "e.txt").write_text("e\n")
    bad = await run("pipeline:\n  - git.commit_push: { message: e }\n", repo)
    assert bad.items[0].port == "fail" and "git push failed" in bad.items[0].value["error"]
    (repo / "e.txt").unlink()
    git(repo, "reset", "-q", "--hard", "HEAD~1")


async def test_commit_push_stops_when_the_commit_fails(tmp_path: Path) -> None:
    result = await run("pipeline:\n  - git.commit_push: { message: x }\n", tmp_path)
    assert result.items[0].port == "fail"


async def test_diff_lists_changed_files_and_optionally_the_patch(repo: Path) -> None:
    (repo / "a.txt").write_text("base\nmore\nlines\n")
    (repo / "bin.dat").write_bytes(b"\x00\x01")
    git(repo, "add", "bin.dat")
    result = await run("pipeline:\n  - git.diff: { patch: true }\n", repo)
    value = result.items[0].value
    assert {f["path"]: (f["additions"], f["deletions"]) for f in value["files"]} == {
        "a.txt": (2, 0),
        "bin.dat": (0, 0),
    }
    assert "+more" in value["patch"]
    plain = await run("pipeline:\n  - git.diff: {}\n", repo)
    assert "patch" not in plain.items[0].value
    failed = await run("pipeline:\n  - git.diff: { base: nope }\n", repo)
    assert failed.items[0].port == "fail"
