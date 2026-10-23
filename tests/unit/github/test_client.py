"""GhCliClient against a stand-in `gh` that answers from canned JSON and records its calls."""

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from whiletrue.github import GhCliClient, GitHubError

SCRIPT = r"""#!__PY__
import json, os, sys
args = sys.argv[1:]
log = os.environ["GH_LOG"]
body = sys.stdin.read() if "--input" in args else ""
open(log, "a").write(json.dumps({"args": args, "body": body}) + "\n")
routes = json.load(open(os.environ["GH_ROUTES"]))
path = args[1] if args and args[0] == "api" else ""
method = args[args.index("--method") + 1] if "--method" in args else "GET"
key = method + " " + path
if key not in routes:
    sys.stderr.write("no route for " + key)
    sys.exit(1)
out = routes[key]
if method == "GET":
    out = [out]  # what --paginate --slurp prints: a list of pages
print(json.dumps(out))
"""


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "gh"
    script.write_text(SCRIPT.replace("__PY__", sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    routes: dict[str, Any] = {}
    routes_file = tmp_path / "routes.json"
    monkeypatch.setenv("GH_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("GH_ROUTES", str(routes_file))

    def set_routes(**r: Any) -> GhCliClient:
        routes.clear()
        routes.update(
            {k.replace("__", " ", 1).replace("_", "/") if False else k: v for k, v in r.items()}
        )
        routes_file.write_text(json.dumps(routes))
        return GhCliClient(str(script))

    def calls() -> list[dict[str, Any]]:
        return [json.loads(line) for line in (tmp_path / "calls.log").read_text().splitlines()]

    return set_routes, calls


def routes(**named: Any) -> dict[str, Any]:
    return named


async def test_lists_issues_with_milestone_labels_and_blockers(gh: Any) -> None:
    set_routes, calls = gh
    client = set_routes(
        **{
            "GET repos/o/r/milestones?state=all": [{"title": "M3", "number": 5}],
            "GET repos/o/r/issues?state=open&per_page=100&milestone=5&labels=a,b": [
                {
                    "number": 1,
                    "title": "T",
                    "body": "spec: a.md",
                    "html_url": "u1",
                    "labels": [{"name": "a"}],
                },
                {"number": 2, "pull_request": {}, "title": "pr", "html_url": "u"},
                {
                    "number": 3,
                    "title": "U",
                    "body": "Blocked by #1",
                    "html_url": "u3",
                    "labels": [],
                },
            ],
            "GET repos/o/r/issues/1/dependencies/blocked_by": [],
            "GET repos/o/r/issues/3/dependencies/blocked_by": [
                {"number": 1, "state": "open"},
                {"number": 9, "state": "closed"},
            ],
        }
    )
    issues = await client.list_issues("o/r", milestone="M3", state="open", labels=["a", "b"])
    assert [(i.number, i.labels, i.blocked_by) for i in issues] == [(1, ["a"], []), (3, [], [1])]
    assert any("--paginate" in c["args"] for c in calls())


async def test_blockers_fall_back_to_the_issue_body_when_the_api_has_none(gh: Any) -> None:
    set_routes, _ = gh
    client = set_routes(
        **{
            "GET repos/o/r/issues?state=open&per_page=100": [
                {"number": 4, "title": "T", "body": "Blocked by #2", "html_url": "u", "labels": []}
            ],
        }
    )
    [issue] = await client.list_issues("o/r", milestone=None, state="open", labels=[])
    assert issue.blocked_by == [2]


async def test_an_unknown_milestone_is_an_error(gh: Any) -> None:
    set_routes, _ = gh
    client = set_routes(**{"GET repos/o/r/milestones?state=all": []})
    with pytest.raises(GitHubError, match='No milestone "M9"'):
        await client.list_issues("o/r", milestone="M9", state="open", labels=[])


PR = {
    "number": 7,
    "html_url": "https://github.com/o/r/pull/7",
    "head": {"ref": "wt/1"},
    "base": {"ref": "main"},
    "title": "T",
    "state": "open",
}


async def test_finds_creates_and_reads_pull_requests(gh: Any) -> None:
    set_routes, calls = gh
    client = set_routes(
        **{
            "GET repos/o/r/pulls?state=all&head=o:wt/1": [PR],
            "GET repos/o/r/pulls?state=all&head=o:wt/9": [],
            "POST repos/o/r/pulls": PR,
            "GET repos/o/r/pulls/7": {**PR, "merged_at": "2026-01-01T00:00:00Z", "state": "closed"},
            "GET repos/o/r/pulls/7/reviews": [
                {"id": 1, "state": "APPROVED", "user": {"login": "a"}, "body": ""},
                {"id": 2, "state": "COMMENTED", "user": {"login": "c"}, "body": "x"},
            ],
        }
    )
    found = await client.find_pr("o/r", "wt/1")
    assert found is not None and (found.number, found.branch, found.state) == (7, "wt/1", "open")
    assert await client.find_pr("o/r", "wt/9") is None
    created = await client.create_pr("o/r", head="wt/1", base="main", title="T", body="B")
    assert created.url.endswith("/pull/7")
    post = next(c for c in calls() if "POST" in c["args"])
    assert json.loads(post["body"]) == {"head": "wt/1", "base": "main", "title": "T", "body": "B"}
    pr = await client.get_pr("o/r", 7)
    assert (pr.state, pr.review) == ("merged", "approved")


async def test_changes_requested_wins_and_carries_its_notes(gh: Any) -> None:
    set_routes, _ = gh
    client = set_routes(
        **{
            "GET repos/o/r/pulls/7": PR,
            "GET repos/o/r/pulls/7/reviews": [
                {"id": 1, "state": "APPROVED", "user": {"login": "a"}, "body": ""},
                {
                    "id": 2,
                    "state": "CHANGES_REQUESTED",
                    "user": {"login": "b"},
                    "body": "add tests",
                },
                {"id": 3, "state": "CHANGES_REQUESTED", "user": {"login": "a"}, "body": ""},
            ],
        }
    )
    pr = await client.get_pr("o/r", 7)
    assert pr.review == "changes_requested" and pr.review_notes == ["add tests"]


async def test_lists_and_replies_to_inline_and_conversation_comments(gh: Any) -> None:
    set_routes, calls = gh
    client = set_routes(
        **{
            "GET repos/o/r/pulls/7/comments": [
                {
                    "id": 11,
                    "user": {"login": "alice"},
                    "body": "why?",
                    "created_at": "2026-01-01T00:00:00Z",
                    "html_url": "h",
                },
                {
                    "id": 12,
                    "in_reply_to_id": 11,
                    "user": {"login": "bob"},
                    "body": "also",
                    "created_at": None,
                },
            ],
            "GET repos/o/r/issues/7/comments": [
                {"id": 21, "user": None, "body": "general", "created_at": "2026-01-02T00:00:00Z"}
            ],
            "POST repos/o/r/pulls/7/comments/11/replies": {"id": 31},
            "POST repos/o/r/issues/7/comments": {"id": 41},
        }
    )
    comments = await client.list_comments("o/r", 7)
    assert [(c.id, c.kind, c.thread_root, c.author) for c in comments] == [
        ("inline:11", "inline", "11", "alice"),
        ("inline:12", "inline", "11", "bob"),
        ("issue:21", "issue", None, "?"),
    ]
    assert comments[0].created_at > 0 and comments[1].created_at == 0
    assert await client.reply("o/r", 7, comments[1], "[whiletrue] ok") == "inline:31"
    assert await client.reply("o/r", 7, comments[2], "thanks") == "issue:41"
    bodies = [json.loads(c["body"]) for c in calls() if "POST" in c["args"]]
    assert bodies == [{"body": "[whiletrue] ok"}, {"body": "thanks"}]


async def test_merges_and_reports_failures(gh: Any) -> None:
    set_routes, calls = gh
    client = set_routes(**{"PUT repos/o/r/pulls/7/merge": {"merged": True}})
    await client.merge_pr("o/r", 7, "squash")
    assert json.loads(calls()[-1]["body"]) == {"merge_method": "squash"}
    with pytest.raises(GitHubError, match="failed"):
        await client.merge_pr("o/r", 8, "squash")


async def test_a_missing_gh_binary_is_reported(tmp_path: Path) -> None:
    with pytest.raises(GitHubError, match="Cannot run"):
        await GhCliClient(str(tmp_path / "nope")).merge_pr("o/r", 1, "squash")
