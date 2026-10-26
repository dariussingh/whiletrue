from __future__ import annotations

import time
from dataclasses import replace

from whiletrue.github.client import GhComment, GhIssue, GhPullRequest, GitHubError


class InMemoryGitHub:
    """A GitHub stand-in for tests and dry runs: a few issues, PRs and comment threads."""

    def __init__(self) -> None:
        self.issues: list[GhIssue] = []
        self.prs: dict[int, GhPullRequest] = {}
        self.comments: dict[int, list[GhComment]] = {}
        self.replies: list[tuple[int, str, str]] = []
        self.merged: list[tuple[int, str]] = []
        self.created: list[int] = []
        self._next_pr = 1
        self._next_comment = 1000

    # ---- helpers for tests --------------------------------------------------------------

    def add_comment(
        self,
        number: int,
        body: str,
        *,
        author: str = "alice",
        kind: str = "inline",
        root: str | None = None,
    ) -> GhComment:
        self._next_comment += 1
        comment_id = str(self._next_comment)
        comment = GhComment(
            id=f"{kind}:{comment_id}",
            kind="inline" if kind == "inline" else "issue",
            author=author,
            body=body,
            created_at=int(time.time() * 1000),
            url=f"https://example.test/pr/{number}#c{comment_id}",
            thread_root=(root or comment_id) if kind == "inline" else None,
        )
        self.comments.setdefault(number, []).append(comment)
        return comment

    def review(self, number: int, state: str | None, notes: list[str] | None = None) -> None:
        pr = self.prs[number]
        pr.review = state  # type: ignore[assignment]
        pr.review_notes = list(notes or [])

    # ---- GitHubClient --------------------------------------------------------------------

    async def list_issues(
        self, repo: str, *, milestone: str | None, state: str, labels: list[str]
    ) -> list[GhIssue]:
        return [replace(i) for i in self.issues if not labels or set(labels) <= set(i.labels)]

    async def find_pr(self, repo: str, head: str) -> GhPullRequest | None:
        found = next((p for p in self.prs.values() if p.branch == head), None)
        return replace(found) if found else None

    async def create_pr(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> GhPullRequest:
        number = self._next_pr
        self._next_pr += 1
        pr = GhPullRequest(number, f"https://example.test/{repo}/pull/{number}", head, base, title)
        self.prs[number] = pr
        self.created.append(number)
        return replace(pr)

    async def get_pr(self, repo: str, number: int) -> GhPullRequest:
        if number not in self.prs:
            raise GitHubError(f"No PR #{number}")
        return replace(self.prs[number])

    async def list_comments(self, repo: str, number: int) -> list[GhComment]:
        return [replace(c) for c in self.comments.get(number, [])]

    async def reply(self, repo: str, number: int, comment: GhComment, body: str) -> str:
        self.replies.append((number, comment.id, body))
        mine = self.add_comment(
            number,
            body,
            author="whiletrue",
            kind=comment.kind,
            root=comment.thread_root,
        )
        return mine.id

    async def merge_pr(self, repo: str, number: int, method: str) -> None:
        if number not in self.prs:
            raise GitHubError(f"No PR #{number}")
        self.prs[number].state = "merged"
        self.merged.append((number, method))
