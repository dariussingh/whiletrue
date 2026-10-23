from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, cast

from whiletrue.github.specs import blocked_by_in_body


class GitHubError(Exception):
    """A GitHub call failed."""


@dataclass
class GhIssue:
    number: int
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=lambda: [])
    blocked_by: list[int] = field(default_factory=lambda: [])


@dataclass
class GhPullRequest:
    number: int
    url: str
    branch: str
    base: str
    title: str
    state: Literal["open", "closed", "merged"] = "open"
    #: The overall review state: approved, changes_requested, or None (no decisive review yet).
    review: Literal["approved", "changes_requested"] | None = None
    #: Bodies of the reviews that requested changes, for a rejection note.
    review_notes: list[str] = field(default_factory=lambda: [])


@dataclass
class GhComment:
    #: Unique within its kind: a review (inline) comment id or an issue comment id.
    id: str
    kind: Literal["inline", "issue"]
    author: str
    body: str
    created_at: int
    url: str | None = None
    #: For inline comments, the id of the first comment in the thread (its own id if first).
    thread_root: str | None = None


class GitHubClient(Protocol):
    async def list_issues(
        self, repo: str, *, milestone: str | None, state: str, labels: list[str]
    ) -> list[GhIssue]: ...

    async def find_pr(self, repo: str, head: str) -> GhPullRequest | None: ...

    async def create_pr(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> GhPullRequest: ...

    async def get_pr(self, repo: str, number: int) -> GhPullRequest: ...

    async def list_comments(self, repo: str, number: int) -> list[GhComment]: ...

    async def reply(self, repo: str, number: int, comment: GhComment, body: str) -> str: ...

    async def merge_pr(self, repo: str, number: int, method: str) -> None: ...


def _login(raw: Any) -> str:
    """The `login` of a possibly-null `user` object."""
    fields = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    user = fields.get("user")
    return str(cast(dict[str, Any], user).get("login", "?")) if isinstance(user, dict) else "?"


def _ms(stamp: str | None) -> int:
    if not stamp:
        return 0
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def repo_from_remote(url: str) -> str | None:
    """`owner/repo` from a git remote URL (ssh or https)."""
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


class GhCliClient:
    """Talks to GitHub through the `gh` CLI, which owns authentication."""

    def __init__(self, command: str = "gh") -> None:
        self._command = command

    async def _run(self, *args: str, stdin: str | None = None) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._command,
                *args,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise GitHubError(f"Cannot run {self._command}: {error}") from error
        out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
            raise GitHubError(f"`gh {' '.join(args[:3])}` failed: {detail[-400:]}")
        return out.decode()

    async def _one(self, path: str) -> Any:
        """A GET that returns a single object (`--slurp` wraps it in a one-element list)."""
        found: Any = await self._api(path)
        if isinstance(found, list) and len(cast(list[Any], found)) > 0:
            first: Any = cast(list[Any], found)[0]
            return first
        return found  # pyright: ignore[reportUnknownVariableType]

    async def _api(
        self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None
    ) -> Any:
        args = ["api", path, "--method", method]
        if method == "GET":
            args += ["--paginate", "--slurp"]
        text = (
            await self._run(*args, "--input", "-", stdin=json.dumps(body))
            if body
            else (await self._run(*args))
        )
        data = json.loads(text) if text.strip() else None
        if method == "GET" and isinstance(data, list):
            # --slurp wraps each page in a list; flatten the pages.
            flat: list[Any] = []
            for page in cast(list[Any], data):
                flat.extend(cast(list[Any], page) if isinstance(page, list) else [page])
            return flat
        return data

    async def _milestone_number(self, repo: str, title: str) -> int:
        for m in await self._api(f"repos/{repo}/milestones?state=all"):
            if m["title"] == title or str(m["number"]) == title:
                return int(m["number"])
        raise GitHubError(f'No milestone "{title}" in {repo}')

    async def list_issues(
        self, repo: str, *, milestone: str | None, state: str, labels: list[str]
    ) -> list[GhIssue]:
        query = f"state={state}&per_page=100"
        if milestone:
            query += f"&milestone={await self._milestone_number(repo, milestone)}"
        if labels:
            query += "&labels=" + ",".join(labels)
        issues: list[GhIssue] = []
        for raw in await self._api(f"repos/{repo}/issues?{query}"):
            if "pull_request" in raw:
                continue
            blocked = await self._blocked_by(repo, int(raw["number"]), raw.get("body") or "")
            issues.append(
                GhIssue(
                    number=int(raw["number"]),
                    title=raw["title"],
                    body=raw.get("body") or "",
                    url=raw["html_url"],
                    labels=[lb["name"] for lb in raw.get("labels", [])],
                    blocked_by=blocked,
                )
            )
        return issues

    async def _blocked_by(self, repo: str, number: int, body: str) -> list[int]:
        try:
            deps = await self._api(f"repos/{repo}/issues/{number}/dependencies/blocked_by")
        except (GitHubError, ValueError):
            return blocked_by_in_body(body)
        return [int(d["number"]) for d in deps if d.get("state", "open") == "open"]

    def _pr(self, raw: dict[str, Any]) -> GhPullRequest:
        merged = bool(raw.get("merged") or raw.get("merged_at"))
        return GhPullRequest(
            number=int(raw["number"]),
            url=raw["html_url"],
            branch=raw["head"]["ref"],
            base=raw["base"]["ref"],
            title=raw.get("title", ""),
            state="merged" if merged else raw.get("state", "open"),
        )

    async def find_pr(self, repo: str, head: str) -> GhPullRequest | None:
        owner = repo.split("/", maxsplit=1)[0]
        found = await self._api(f"repos/{repo}/pulls?state=all&head={owner}:{head}")
        return self._pr(found[0]) if found else None

    async def create_pr(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> GhPullRequest:
        raw = await self._api(
            f"repos/{repo}/pulls",
            method="POST",
            body={"head": head, "base": base, "title": title, "body": body},
        )
        return self._pr(raw)

    async def get_pr(self, repo: str, number: int) -> GhPullRequest:
        pr = self._pr(await self._one(f"repos/{repo}/pulls/{number}"))
        latest: dict[str, tuple[int, str, str]] = {}
        for review in await self._api(f"repos/{repo}/pulls/{number}/reviews"):
            state = review.get("state", "")
            if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                user = _login(review)
                latest[user] = (int(review["id"]), state, review.get("body") or "")
        states = {state for _, state, _ in latest.values()}
        if "CHANGES_REQUESTED" in states:
            pr.review = "changes_requested"
            pr.review_notes = [b for _, s, b in latest.values() if s == "CHANGES_REQUESTED" and b]
        elif "APPROVED" in states:
            pr.review = "approved"
        return pr

    async def list_comments(self, repo: str, number: int) -> list[GhComment]:
        comments: list[GhComment] = []
        for raw in await self._api(f"repos/{repo}/pulls/{number}/comments"):
            root = raw.get("in_reply_to_id") or raw["id"]
            comments.append(
                GhComment(
                    id=f"inline:{raw['id']}",
                    kind="inline",
                    author=_login(raw),
                    body=raw.get("body") or "",
                    created_at=_ms(raw.get("created_at")),
                    url=raw.get("html_url"),
                    thread_root=str(root),
                )
            )
        for raw in await self._api(f"repos/{repo}/issues/{number}/comments"):
            comments.append(
                GhComment(
                    id=f"issue:{raw['id']}",
                    kind="issue",
                    author=_login(raw),
                    body=raw.get("body") or "",
                    created_at=_ms(raw.get("created_at")),
                    url=raw.get("html_url"),
                )
            )
        return comments

    async def reply(self, repo: str, number: int, comment: GhComment, body: str) -> str:
        if comment.kind == "inline":
            root = comment.thread_root or comment.id.split(":", 1)[1]
            raw = await self._api(
                f"repos/{repo}/pulls/{number}/comments/{root}/replies",
                method="POST",
                body={"body": body},
            )
            return f"inline:{raw['id']}"
        raw = await self._api(
            f"repos/{repo}/issues/{number}/comments", method="POST", body={"body": body}
        )
        return f"issue:{raw['id']}"

    async def merge_pr(self, repo: str, number: int, method: str) -> None:
        await self._api(
            f"repos/{repo}/pulls/{number}/merge", method="PUT", body={"merge_method": method}
        )
