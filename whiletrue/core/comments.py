"""Comments are human (or reviewer) input attached to an item; every one must get an answer.

Providers fetch comments from wherever they live (GitHub PR threads, a local file) and post
replies. The store remembers them across runs, so a comment is answered once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from whiletrue.core.history import CommentRecord

if TYPE_CHECKING:
    from collections.abc import Callable

    from whiletrue.core.registry import Registry
    from whiletrue.core.store import StateStore

#: Prefix on every reply the pipeline posts, so people always know who wrote what.
REPLY_PREFIX = "[whiletrue]"


@dataclass(frozen=True)
class IncomingComment:
    external_id: str
    author: str
    body: str
    created_at: int
    thread: str | None = None
    url: str | None = None
    kind: Literal["human", "reviewer"] = "human"


@dataclass
class CommentsContext:
    cwd: str
    now: Callable[[], int]
    item_id: str
    item: dict[str, Any]
    #: What the step before the gate produced (for GitHub: the opened PR).
    input: Any = None


class CommentsProvider(Protocol):
    """Where comments come from and where replies go. Providers never resolve threads."""

    async def fetch(self, ctx: CommentsContext) -> list[IncomingComment]: ...

    async def reply(self, comment: CommentRecord, body: str, ctx: CommentsContext) -> str | None:
        """Posts a reply in the comment's thread; returns the new comment's id if there is one."""
        ...


def with_prefix(body: str) -> str:
    return body if body.startswith(REPLY_PREFIX) else f"{REPLY_PREFIX} {body}"


async def sync_comments(
    registry: Registry, store: StateStore, ctx: CommentsContext
) -> list[CommentRecord]:
    """Pulls new comments from every provider into the store; returns the ones just added."""
    added: list[CommentRecord] = []
    for name, provider in registry.providers("comments"):
        incoming = await provider.fetch(ctx)
        for comment in incoming:
            record = store.add_comment(
                item_id=ctx.item_id,
                source=name,
                external_id=comment.external_id,
                author=comment.author,
                body=comment.body,
                created_at=comment.created_at,
                thread=comment.thread,
                url=comment.url,
                kind=comment.kind,
            )
            if record is not None:
                added.append(record)
    return added
