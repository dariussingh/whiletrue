import subprocess
from pathlib import Path
from typing import Any

import pytest

from whiletrue.control import control_plugin
from whiletrue.core import (
    CommentRecord,
    CommentsContext,
    MemoryStore,
    ParkRecord,
    Registry,
    WakeContext,
    fetch_items,
    parse_pipeline,
    run_pipeline,
)
from whiletrue.github import GhIssue, InMemoryGitHub, github_plugin


def setup(gh: InMemoryGitHub | None = None) -> tuple[Registry, InMemoryGitHub]:
    gh = gh or InMemoryGitHub()
    registry = Registry()
    registry.use(control_plugin)
    registry.use(github_plugin(gh))
    return registry, gh


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def make_remote_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    git(work, "checkout", "-b", "main")
    (work / "a.txt").write_text("base\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "base")
    git(work, "push", "-u", "origin", "main")
    return origin, work


# ---- source ---------------------------------------------------------------------------------


async def test_issues_become_items_with_blockers_inside_the_set_as_dependencies(
    tmp_path: Path,
) -> None:
    registry, gh = setup()
    gh.issues = [
        GhIssue(1, "A", "spec: specs/a.md#L1-L9", "u1", ["m3"]),
        GhIssue(2, "B", "", "u2", ["m3"], blocked_by=[1, 99]),
        GhIssue(3, "C", "", "u3", ["other"]),
    ]
    extra = {"source": {"github_issues": {"repo": "o/r", "milestone": "M3", "labels": ["m3"]}}}
    items = await fetch_items(registry, extra, str(tmp_path))
    assert items is not None
    assert [(i.id, i.depends_on) for i in items] == [("1", []), ("2", ["1"])]
    assert items[0].payload["spec_refs"] == ["specs/a.md#L1-L9"]
    assert items[0].payload["repo"] == "o/r" and items[0].payload["title"] == "A"

    plain = {"source": {"github_issues": {"repo": "o/r", "depends_on": None, "labels": ["m3"]}}}
    again = await fetch_items(registry, plain, str(tmp_path))
    assert again is not None and [i.depends_on for i in again] == [[], []]


async def test_the_repo_is_read_from_the_origin_remote(tmp_path: Path) -> None:
    registry, gh = setup()
    gh.issues = [GhIssue(1, "A", "", "u")]
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "git@github.com:owner/name.git")
    items = await fetch_items(registry, {"source": {"github_issues": {}}}, str(tmp_path))
    assert items is not None and items[0].payload["repo"] == "owner/name"


async def test_a_missing_remote_asks_for_the_repo(tmp_path: Path) -> None:
    registry, _ = setup()
    with pytest.raises(Exception, match="set `repo: owner/name`"):
        await fetch_items(registry, {"source": {"github_issues": {}}}, str(tmp_path))


# ---- open_pr, merge (journaled effects) -----------------------------------------------------

PIPELINE = """pipeline:
  - github.open_pr: { repo: o/r, head: "wt/{{item.id}}", push: false, closes: 5, title: "Do it" }
  - github.merge: { repo: o/r, lock: "repo:main" }
"""


async def run(registry: Registry, text: str, store: MemoryStore | None = None, **kw: Any):
    return await run_pipeline(
        registry=registry, config=parse_pipeline(text, "t.yaml"), store=store or MemoryStore(), **kw
    )


async def test_open_pr_and_merge_each_happen_once_across_runs() -> None:
    registry, gh = setup()
    store = MemoryStore()
    first = await run(registry, PIPELINE, store)
    second = await run(registry, PIPELINE, store)
    assert first.status == second.status == "done"
    assert gh.created == [1] and gh.merged == [(1, "squash")]
    value = first.items[0].value
    assert value["state"] == "merged" and value["repo"] == "o/r" and value["branch"] == "wt/main"
    assert store.effect_get("github.open_pr:o/r:wt/main") is not None
    assert store.effect_get("github.merge:o/r:1") is not None


async def test_open_pr_reuses_a_pr_that_already_exists_for_the_branch() -> None:
    registry, gh = setup()
    await gh.create_pr("o/r", head="wt/main", base="main", title="old", body="")
    result = await run(registry, PIPELINE)
    assert result.status == "done" and gh.created == [1]


async def test_open_pr_title_defaults_to_the_item_title_and_body_closes_the_issue() -> None:
    registry, gh = setup()
    bodies: list[str] = []
    original = gh.create_pr

    async def spy(repo: str, *, head: str, base: str, title: str, body: str):
        bodies.append(body)
        return await original(repo, head=head, base=base, title=title, body=body)

    gh.create_pr = spy  # type: ignore[method-assign]
    text = "pipeline:\n  - github.open_pr: { repo: o/r, head: b, push: false, closes: 5, body: text }\n"
    from whiletrue.core import RunItem

    result = await run(registry, text, items=[RunItem("1", {"title": "From the issue"})])
    assert result.status == "done"
    assert bodies == ["text\n\nCloses #5"]
    assert gh.prs[1].title == "From the issue"


async def test_merge_reports_closed_prs_missing_numbers_and_already_merged_prs() -> None:
    registry, gh = setup()
    pr = await gh.create_pr("o/r", head="x", base="main", title="t", body="")
    gh.prs[pr.number].state = "closed"
    closed = await run(registry, "pipeline:\n  - github.merge: { repo: o/r, number: 1 }\n")
    assert closed.items[0].port == "fail" and "closed" in closed.items[0].value["error"]
    none = await run(registry, "pipeline:\n  - github.merge: { repo: o/r }\n")
    assert none.items[0].port == "fail" and "needs a PR number" in none.items[0].value["error"]
    gh.prs[1].state = "merged"
    merged = await run(registry, "pipeline:\n  - github.merge: { repo: o/r, number: 1 }\n")
    assert merged.status == "done" and gh.merged == []


async def test_github_errors_become_failures() -> None:
    registry, _ = setup()
    result = await run(registry, "pipeline:\n  - github.merge: { repo: o/r, number: 42 }\n")
    assert result.items[0].port == "fail" and "No PR #42" in result.items[0].value["error"]


async def test_open_pr_pushes_the_branch_first(tmp_path: Path) -> None:
    registry, gh = setup()
    origin, work = make_remote_and_clone(tmp_path)
    git(work, "checkout", "-b", "wt/1")
    (work / "b.txt").write_text("feature\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "feature")
    text = "pipeline:\n  - github.open_pr: { repo: o/r, head: wt/1 }\n"
    result = await run(registry, text, cwd=str(work))
    assert result.status == "done" and gh.created == [1]
    assert "wt/1" in git(tmp_path, "--git-dir", str(origin), "branch")

    failing = "pipeline:\n  - github.open_pr: { repo: o/r, head: not-a-branch }\n"
    bad = await run(registry, failing, cwd=str(work))
    assert bad.items[0].port == "fail" and "git push failed" in bad.items[0].value["error"]


# ---- sync_base ------------------------------------------------------------------------------


async def test_sync_base_rebases_and_pushes(tmp_path: Path) -> None:
    registry, _ = setup()
    origin, work = make_remote_and_clone(tmp_path)
    git(work, "checkout", "-b", "wt/1")
    (work / "mine.txt").write_text("mine\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "mine")
    git(work, "push", "-u", "origin", "wt/1")
    other = tmp_path / "other"
    subprocess.run(  # noqa: ASYNC221
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
    (other / "upstream.txt").write_text("up\n")
    git(other, "add", ".")
    git(other, "commit", "-m", "upstream")
    git(other, "push", "origin", "main")

    result = await run(registry, "pipeline:\n  - github.sync_base: {}\n", cwd=str(work))
    assert result.status == "done"
    assert (work / "upstream.txt").exists() and (work / "mine.txt").exists()
    log = git(work, "log", "--oneline", "origin/main..HEAD")
    assert "mine" in log and "upstream" not in log
    remote_log = git(tmp_path, "--git-dir", str(origin), "log", "--oneline", "wt/1")
    assert "upstream" in remote_log  # force-pushed with the rebased history

    again = await run(registry, "pipeline:\n  - github.sync_base: {}\n", cwd=str(work))
    assert again.status == "done"  # nothing to do the second time


async def test_sync_base_parks_the_item_on_a_conflict_and_leaves_the_tree_clean(
    tmp_path: Path,
) -> None:
    registry, _ = setup()
    origin, work = make_remote_and_clone(tmp_path)
    git(work, "checkout", "-b", "wt/1")
    (work / "a.txt").write_text("mine\n")
    git(work, "commit", "-am", "mine")
    other = tmp_path / "other"
    subprocess.run(  # noqa: ASYNC221
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
    (other / "a.txt").write_text("theirs\n")
    git(other, "commit", "-am", "theirs")
    git(other, "push", "origin", "main")

    result = await run(registry, "pipeline:\n  - github.sync_base: {}\n", cwd=str(work))
    assert result.status == "parked"
    value = result.items[0].value
    assert value["files"] == ["a.txt"] and "conflicts" in value["reason"]
    assert git(work, "status", "--porcelain") == ""
    assert (work / "a.txt").read_text() == "mine\n"


async def test_sync_base_fails_when_the_fetch_fails(tmp_path: Path) -> None:
    registry, _ = setup()
    git(tmp_path, "init", "-q")
    result = await run(registry, "pipeline:\n  - github.sync_base: {}\n", cwd=str(tmp_path))
    assert result.items[0].port == "fail"


# ---- wake conditions ------------------------------------------------------------------------


def wake_ctx(registry: Registry, input: Any, item: dict[str, Any] | None = None) -> WakeContext:
    park = ParkRecord(1, "r", "7", "gate", "pr", {}, "waiting", 0)
    return WakeContext(".", lambda: 0, "7", park, registry, item or {}, input)


async def test_review_wake_maps_github_reviews_to_decisions() -> None:
    registry, gh = setup()
    await gh.create_pr("o/r", head="b", base="main", title="t", body="")
    checker = registry.get_provider("wake", "github.pr_review")
    ctx = wake_ctx(registry, {"number": 1, "repo": "o/r"})
    assert await checker.check("approved", ctx) is None
    gh.review(1, "approved")
    approved = await checker.check("approved", ctx)
    assert approved is not None and (approved.decision, approved.by) == ("approved", "github")
    assert await checker.check("changes_requested", ctx) is None
    gh.review(1, "changes_requested", ["add tests", "rename x"])
    rejected = await checker.check(None, ctx)
    assert rejected is not None and rejected.decision == "rejected"
    assert rejected.note == "add tests\n\nrename x"
    assert await checker.check("approved", ctx) is None
    gh.review(1, "changes_requested", [])
    blank = await checker.check("any", ctx)
    assert blank is not None and blank.note == "changes requested on GitHub"


async def test_wakes_need_a_pr_to_look_at_and_merge_wake_sees_merges() -> None:
    registry, gh = setup()
    review = registry.get_provider("wake", "github.pr_review")
    merged = registry.get_provider("wake", "github.pr_merged")
    assert await review.check(None, wake_ctx(registry, "no pr here")) is None
    assert await merged.check(None, wake_ctx(registry, None)) is None
    await gh.create_pr("o/r", head="b", base="main", title="t", body="")
    ctx = wake_ctx(registry, None, {"pr": 1, "repo": "o/r"})
    assert await merged.check(None, ctx) is None
    gh.prs[1].state = "merged"
    result = await merged.check(None, ctx)
    assert result is not None and result.decision == "approved"


# ---- comments provider ----------------------------------------------------------------------


def comments_ctx(input: Any) -> CommentsContext:
    return CommentsContext(".", lambda: 0, "7", {"id": "7"}, input)


async def test_fetches_review_comments_but_never_our_own_replies() -> None:
    registry, gh = setup()
    gh.add_comment(1, "why a global?", author="alice")
    gh.add_comment(1, "[whiletrue] because", author="alice")
    gh.add_comment(1, "lint failed", author="ci[bot]", kind="issue")
    provider = registry.get_provider("comments", "github_pr")
    found = await provider.fetch(comments_ctx({"number": 1, "repo": "o/r"}))
    assert [(c.author, c.kind, c.body) for c in found] == [
        ("alice", "human", "why a global?"),
        ("ci[bot]", "reviewer", "lint failed"),
    ]
    assert found[0].thread is not None and found[0].thread.startswith("o/r#1:inline:")
    assert await provider.fetch(comments_ctx("no pr yet")) == []


async def test_replies_go_to_the_right_thread() -> None:
    registry, gh = setup()
    inline = gh.add_comment(1, "q1", kind="inline")
    issue = gh.add_comment(1, "q2", kind="issue")
    provider = registry.get_provider("comments", "github_pr")
    fetched = await provider.fetch(comments_ctx({"number": 1, "repo": "o/r"}))

    def record(index: int) -> CommentRecord:
        c = fetched[index]
        return CommentRecord(
            index + 1, "7", "github_pr", c.external_id, c.author, c.body, 0, thread=c.thread
        )

    ref = await provider.reply(record(0), "[whiletrue] a1", comments_ctx(None))
    await provider.reply(record(1), "[whiletrue] a2", comments_ctx(None))
    assert gh.replies == [(1, inline.id, "[whiletrue] a1"), (1, issue.id, "[whiletrue] a2")]
    assert ref is not None and ref.startswith("inline:")
    local = CommentRecord(9, "7", "local", "x", "me", "hi", 0)
    assert await provider.reply(local, "x", comments_ctx(None)) is None
    odd = CommentRecord(10, "7", "github_pr", "x", "me", "hi", 0, thread="garbage")
    assert await provider.reply(odd, "x", comments_ctx(None)) is None
