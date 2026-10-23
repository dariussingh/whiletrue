from __future__ import annotations

from pydantic import BaseModel, Field

from whiletrue.core import (
    ActionModule,
    CommentsContext,
    Emit,
    EmptyConfig,
    Ports,
    PortSpec,
    StepContext,
    define_action,
    with_prefix,
)
from whiletrue.core.module import PortValues

COMMENT_KINDS = ("question", "change_request", "nit", "approval", "out_of_scope")


class CommentReply(BaseModel):
    comment_id: int
    #: How the comment was classified.
    kind: str = "question"
    #: What was said back: the answer, "fixed in abc123", or "declined because ...".
    reply: str = Field(min_length=1)


class CommentReplies(BaseModel):
    """What a responder agent returns: one reply per comment it was given."""

    replies: list[CommentReply]


async def _reply(input: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    store = ctx.store
    if store is None:
        return Emit("fail", {"error": "comments.reply needs a state store"})
    payload = CommentReplies.model_validate(input.get("in") or {})
    known = {c.id: c for c in store.list_comments(ctx.item_id)}
    item = ctx.scope.get("item") or {"id": ctx.item_id}
    posted: list[int] = []
    for reply in payload.replies:
        comment = known.get(reply.comment_id)
        if comment is None:
            return Emit("fail", {"error": f"No comment {reply.comment_id} on {ctx.item_id}"})
        if comment.answered:
            continue
        key = f"reply:{ctx.item_id}:{comment.source}:{comment.external_id}"
        effect = store.effect_get(key)
        body = with_prefix(reply.reply)
        if effect is None:
            provider = ctx.registry.get_provider("comments", comment.source)
            store.effect_put(
                key, run_id=ctx.run_id, status="started", ref=None, created_at=ctx.now()
            )
            if provider is not None:
                ref = await provider.reply(
                    comment, body, CommentsContext(ctx.cwd, ctx.now, ctx.item_id, item)
                )
            else:
                ref = None  # a local comment: the answer is only recorded
            store.effect_put(key, run_id=ctx.run_id, status="done", ref=ref, created_at=ctx.now())
        # A "started" effect with no "done" means a crash mid-post: do not risk a duplicate.
        store.answer_comment(comment.id, body)
        ctx.bus.emit(
            "comment.answered",
            ctx.run_id,
            {"comment": comment.id, "source": comment.source, "kind": reply.kind},
            item_id=ctx.item_id,
        )
        posted.append(comment.id)
    return Emit("ok", {"answered": posted})


#: Posts the agent's replies in each comment's own thread (prefixed `[whiletrue]`, once only).
comments_reply_module: ActionModule[EmptyConfig] = define_action(
    ActionModule(
        id="comments.reply",
        config=EmptyConfig,
        ports=Ports(inputs=[PortSpec("in", "CommentReplies")], outputs=[PortSpec("out", "Any")]),
        effects="side-effect",
        cacheable=False,
        idempotency_key=lambda _i, ctx: f"comments.reply:{ctx.item_id}:{ctx.node_id}",
        run=_reply,
    )
)


async def _answered(_input: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    if ctx.store is None:
        return Emit("fail", {"error": "gate.comments_answered needs a state store"})
    open_comments = ctx.store.list_comments(ctx.item_id, unanswered_only=True)
    if open_comments:
        ids = ", ".join(f"#{c.id} ({c.author})" for c in open_comments)
        return Emit(
            "fail",
            {"error": f"{len(open_comments)} comment(s) still have no reply: {ids}"},
        )
    return Emit("ok", _input.get("in"))


#: Fails while any comment on the item (human or reviewer) has no reply.
gate_comments_answered_module: ActionModule[EmptyConfig] = define_action(
    ActionModule(
        id="gate.comments_answered",
        config=EmptyConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="deterministic",
        cacheable=False,
        run=_answered,
    )
)
