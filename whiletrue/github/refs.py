"""Where a PR or repo is: detecting the repo, reading a PR reference, and thread ids."""

from __future__ import annotations

from typing import Any, cast

from whiletrue.basics.process import run_command
from whiletrue.github.client import GhComment, GitHubError, repo_from_remote


async def detectrepo(cwd: str) -> str:
    result = await run_command("git", ["remote", "get-url", "origin"], cwd=cwd)
    repo = repo_from_remote(result.stdout) if result.code == 0 else None
    if repo is None:
        raise GitHubError("Cannot tell which repository this is: set `repo: owner/name`")
    return repo


async def resolve_repo(configured: str | None, cwd: str) -> str:
    return configured or await detectrepo(cwd)


def pr_ref(input: Any, item: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """The (repo, PR number) a step or a wake condition is about: the PR the previous step made."""
    source = cast(dict[str, Any], input) if isinstance(input, dict) else {}
    number = source.get("number") or (item or {}).get("pr")
    repo = source.get("repo") or (item or {}).get("repo")
    return (str(repo) if repo else None), (int(number) if number else None)


def encode_thread(repo: str, number: int, comment: GhComment) -> str:
    return f"{repo}#{number}:{comment.id}:{comment.thread_root or ''}"


def decode_thread(thread: str | None) -> tuple[str, int, GhComment] | None:
    if not thread or "#" not in thread:
        return None
    head, _, rest = thread.partition("#")
    number, _, tail_ = rest.partition(":")
    comment_id, _, root = tail_.rpartition(":")
    kind = "inline" if comment_id.startswith("inline:") else "issue"
    comment = GhComment(comment_id, kind, "", "", 0, thread_root=root or None)  # type: ignore[arg-type]
    return head, int(number), comment
