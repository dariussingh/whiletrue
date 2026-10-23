"""M4 exit: one issue goes open -> PR -> human review comments answered -> merged.

Real pieces: the CLI, the runtime with park/resume, the issue-loop and review-respond recipes,
SQLite, git (a local bare repo as `origin`). Stand-ins: the agent (a fake `claude`) and GitHub (an
in-memory client), so it runs offline and costs nothing.
"""

import stat
import subprocess
import sys
from pathlib import Path

import pytest

from whiletrue.cli import EXIT, Io, main
from whiletrue.github import GhIssue, InMemoryGitHub

FAKE_CLAUDE = r"""#!__PY__
import json, re, subprocess, sys
prompt = sys.stdin.read()
git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def result(**extra):
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                      "total_cost_usd": 0.01, "num_turns": 1, "session_id": "s", **extra}))


def commit(name, text, message):
    open(name, "w").write(text)
    subprocess.run(git + ["add", name], check=True)
    subprocess.run(git + ["commit", "-m", message], check=True, capture_output=True)


if "IMPLEMENT" in prompt:
    commit("feature.txt", "feature\n", "implement the feature")
    result()
elif "FIX" in prompt:
    commit("fix.txt", "fix\n", "address the review")
    result()
elif "RESPOND" in prompt:
    payload = json.loads(prompt.split("INPUT:", 1)[1].strip().splitlines()[0])
    replies = [
        {"comment_id": c["id"], "kind": "question", "reply": "Answer to: " + c["body"]}
        for c in payload["comments"]
    ]
    result(structured_output={"replies": replies})
else:
    result()
"""

PIPELINE = """name: issues
source: { github_issues: { repo: o/r } }
pipeline:
  - id: issue
    composite:
      uses: "recipe:issue-loop"
      params:
        implementer: ./agents/implement.md
        fixer: ./agents/fix.md
        responder: ./agents/respond.md
        repo: o/r
        gate: "test -f feature.txt"
"""


def agent(name: str, body: str, extra: str = "") -> str:
    return (
        f"---\nagent: {name}\nrunner: claude-code-headless\n{extra}"
        "limits: { budget_usd: 1, turns: 5, wall: 1m }\n---\n" + body + "\n"
    )


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
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    git(work, "checkout", "-b", "main")
    (work / "README.md").write_text("project\n")
    (work / "agents").mkdir()
    (work / "agents" / "implement.md").write_text(agent("implement", "IMPLEMENT {{item.title}}"))
    (work / "agents" / "fix.md").write_text(agent("fix", "FIX the review: {{input}}"))
    (work / "agents" / "respond.md").write_text(
        agent("respond", "RESPOND\nINPUT: {{input}}", "outputs: { replies: CommentReplies }\n")
    )
    (work / "whiletrue.yaml").write_text(PIPELINE)
    (work / ".gitignore").write_text(".whiletrue/\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "base")
    git(work, "push", "-u", "origin", "main")

    fake = tmp_path / "fake-claude"
    fake.write_text(FAKE_CLAUDE.replace("__PY__", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", str(fake))

    gh = InMemoryGitHub()
    gh.issues = [GhIssue(12, "Add the feature", "spec: specs/feature.md#L1-L5", "https://x/12")]

    class Session:
        dir = work
        github = gh

        def __init__(self) -> None:
            self.out = ""
            self.err = ""

        async def __call__(self, *args: str) -> int:
            self.out = self.err = ""

            def out(text: str) -> None:
                self.out += text

            def err(text: str) -> None:
                self.err += text

            return await main(list(args), Io(cwd=str(work), out=out, err=err, github=gh))

    return Session(), origin


async def test_one_issue_goes_from_open_to_merged_with_every_comment_answered(project) -> None:
    cli, origin = project
    gh: InMemoryGitHub = cli.github

    # 1. Implement on a branch, open the PR, park at the review gate.
    assert await cli("validate") == EXIT.ok, cli.err
    assert await cli("run") == EXIT.parked, cli.err
    assert gh.created == [1] and gh.prs[1].branch == "wt/12"
    assert "wt/12" in git(cli.dir, "--git-dir", str(origin), "branch")
    assert (cli.dir / "feature.txt").exists()
    await cli("status")
    assert "pr at issue.review" in cli.out

    # 2. A reviewer and the author comment; the pipeline answers each one in its own thread.
    gh.add_comment(1, "why a global?", author="alice")
    assert await cli("comment", "12", "please also document it") == EXIT.ok
    assert await cli("resume") == EXIT.parked, cli.err
    assert gh.replies == [(1, gh.comments[1][0].id, "[whiletrue] Answer to: why a global?")]
    await cli("history", "12")
    assert "Answer to: please also document it" in cli.out
    assert "Answer to: why a global?" in cli.out

    # 3. The reviewer asks for changes; the fixer runs and the PR is queued for review again.
    assert await cli("reject", "12", "--note", "add a test") == EXIT.ok
    assert await cli("resume") == EXIT.parked, cli.err
    assert (cli.dir / "fix.txt").exists()
    assert gh.merged == []
    await cli("status")
    assert "pr at issue.review" in cli.out  # a second review of the fixed PR

    # 4. Approved: rebase onto main, then a final approval gate before the merge.
    assert await cli("approve", "12") == EXIT.ok
    assert await cli("resume") == EXIT.parked, cli.err
    await cli("status")
    assert "merge at issue.merge_ok" in cli.out
    assert gh.merged == []

    # 5. Approve the merge: it happens once.
    assert await cli("approve", "12") == EXIT.ok
    assert await cli("resume") == EXIT.ok, cli.err
    assert gh.merged == [(1, "squash")]
    assert gh.prs[1].state == "merged"

    await cli("history", "12")
    timeline = cli.out
    assert "rejected by" in timeline and "add a test" in timeline
    assert timeline.count("approved by") == 2

    # Nothing was answered twice and nothing was merged twice.
    assert len(gh.replies) == 1 and gh.created == [1]


async def test_an_approval_on_github_wakes_the_gate_by_itself(project) -> None:
    cli, _ = project
    gh: InMemoryGitHub = cli.github
    assert await cli("run") == EXIT.parked
    gh.review(1, "approved")
    assert await cli("resume") == EXIT.parked  # now parked at the merge gate
    await cli("history", "12")
    assert "approved by github" in cli.out
