"""The GitHub source, comments provider and wake conditions."""

from __future__ import annotations

from typing import Any

from whiletrue.core import (
    CommentRecord,
    CommentsContext,
    IncomingComment,
    RunItem,
    WakeContext,
    WakeResult,
)
from whiletrue.github.client import GitHubClient
from whiletrue.github.refs import decode_thread, encode_thread, pr_ref, resolve_repo
from whiletrue.github.specs import parse_spec_refs


class GithubIssuesSource:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def fetch(self, config: dict[str, Any], cwd: str) -> list[RunItem]:
        repo = await resolve_repo(config.get("repo"), cwd)
        issues = await self._client.list_issues(
            repo,
            milestone=config.get("milestone"),
            state=str(config.get("state", "open")),
            labels=list(config.get("labels") or []),
        )
        numbers = {i.number for i in issues}
        use_blocked_by = config.get("depends_on", "blocked_by") == "blocked_by"
        return [
            RunItem(
                str(i.number),
                {
                    "number": i.number,
                    "title": i.title,
                    "body": i.body,
                    "labels": i.labels,
                    "url": i.url,
                    "repo": repo,
                    "spec_refs": parse_spec_refs(i.body),
                },
                [str(b) for b in i.blocked_by if b in numbers] if use_blocked_by else [],
            )
            for i in issues
        ]


class GithubPrComments:
    """Review threads and conversation comments on the PR the item opened."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def fetch(self, ctx: CommentsContext) -> list[IncomingComment]:
        repo, number = pr_ref(ctx.input, ctx.item)
        if number is None:
            return []
        repo = await resolve_repo(repo, ctx.cwd)
        found = await self._client.list_comments(repo, number)
        return [
            IncomingComment(
                external_id=c.id,
                author=c.author,
                body=c.body,
                created_at=c.created_at,
                thread=encode_thread(repo, number, c),
                url=c.url,
                kind="reviewer" if c.author.endswith("[bot]") else "human",
            )
            for c in found
            if not c.body.lstrip().startswith("[whiletrue]")
        ]

    async def reply(self, comment: CommentRecord, body: str, ctx: CommentsContext) -> str | None:
        decoded = decode_thread(comment.thread)
        if decoded is None:
            return None
        repo, number, target = decoded
        return await self._client.reply(repo, number, target, body)


class GithubReviewWake:
    """`github.pr_review`: `approved`, `changes_requested`, or omitted for either."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        repo, number = pr_ref(ctx.input, ctx.item)
        if number is None:
            return None
        pr = await self._client.get_pr(await resolve_repo(repo, ctx.cwd), number)
        wanted = {
            None: {"approved", "changes_requested"},
            "any": {"approved", "changes_requested"},
        }.get(arg, {str(arg)})
        if pr.review == "approved" and "approved" in wanted:
            return WakeResult("approved", "approved on GitHub", by="github")
        if pr.review == "changes_requested" and "changes_requested" in wanted:
            note = "\n\n".join(pr.review_notes) or "changes requested on GitHub"
            return WakeResult("rejected", note, by="github")
        return None


class GithubMergedWake:
    """`github.pr_merged`: someone merged the PR outside whiletrue."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        repo, number = pr_ref(ctx.input, ctx.item)
        if number is None:
            return None
        pr = await self._client.get_pr(await resolve_repo(repo, ctx.cwd), number)
        return (
            WakeResult("approved", "merged on GitHub", by="github")
            if pr.state == "merged"
            else None
        )
