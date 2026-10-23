import asyncio
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from whiletrue.basics import basics_plugin
from whiletrue.control import control_plugin
from whiletrue.core import (
    ActionModule,
    CommentRecord,
    CommentsContext,
    Emit,
    IncomingComment,
    MemoryStore,
    Ports,
    PortSpec,
    Registry,
    StepContext,
    define_action,
    load_recording,
    parse_pipeline,
    run_pipeline,
    validate_pipeline,
)
from whiletrue.core.module import PortValues


class Noop(BaseModel):
    text: str = ""


ran: list[str] = []


async def mark(input: PortValues, ctx: StepContext[Noop]) -> Emit:
    ran.append(ctx.config.text)
    return Emit("ok", input.get("in"))


async def answer_all(input: PortValues, ctx: StepContext[Noop]) -> Emit:
    comments = input["in"]["comments"]
    return Emit(
        "ok",
        {
            "replies": [
                {"comment_id": c["id"], "kind": "question", "reply": "because"} for c in comments
            ]
        },
    )


class FakeComments:
    def __init__(self) -> None:
        self.incoming: list[IncomingComment] = []
        self.replies: list[tuple[str, str]] = []

    async def fetch(self, ctx: CommentsContext) -> list[IncomingComment]:
        return list(self.incoming)

    async def reply(self, comment: CommentRecord, body: str, ctx: CommentsContext) -> str | None:
        self.replies.append((comment.external_id, body))
        return f"reply-{comment.external_id}"


def registry(comments: FakeComments | None = None) -> Registry:
    r = Registry()
    r.use(control_plugin)
    r.use(basics_plugin)
    ports = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])
    r.register_action(
        define_action(
            ActionModule(
                id="mark",
                config=Noop,
                ports=ports,
                effects="deterministic",
                cacheable=False,
                run=mark,
            )
        )
    )
    r.register_action(
        define_action(
            ActionModule(
                id="answer_all", config=Noop, ports=ports, effects="deterministic", run=answer_all
            )
        )
    )
    if comments is not None:
        r.register_provider("comments", "github_pr", comments)
    return r


def pipeline(body: str, header: str = "") -> Any:
    return parse_pipeline(f"{header}pipeline:\n{body}", "t.yaml")


GATE = """  - mark: { text: before }
  - id: review
    human.approve: { what: pr, poll: 10ms }
  - mark: { text: after }
"""

# A wired target sits last: the approved path never reaches it only because it is the end.
WIRED = """  - mark: { text: before }
  - id: review
    human.approve: { what: pr, poll: 10ms }
    wires: { rejected: fix }
  - id: fix
    mark: { text: fix }
"""


async def test_a_gate_parks_the_item_when_not_watching() -> None:
    ran.clear()
    store = MemoryStore()
    result = await run_pipeline(registry=registry(), config=pipeline(GATE), store=store)
    assert result.status == "parked"
    assert ran == ["before"]
    [park] = store.list_parks("main")
    assert (park.what, park.node_path, park.decision) == ("pr", "review", None)
    assert park.run_id == result.run_id
    assert store.list_events(result.run_id, type="park.created")
    out = result.items[0].value
    assert out["what"] == "pr" and out["park"] == park.id


async def test_a_decided_park_lets_the_resumed_run_through() -> None:
    ran.clear()
    store = MemoryStore()
    reg = registry()
    first = await run_pipeline(registry=reg, config=pipeline(GATE), store=store)
    [park] = store.list_parks("main")
    store.decide_park(park.id, "approved", decided_by="me", decided_at=5, notes=None)

    recording = load_recording(store, first.run_id)
    second = await run_pipeline(
        registry=reg,
        config=pipeline(GATE),
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
        lineage=recording.lineage,
    )
    assert second.status == "done"
    assert ran == ["before", "after"]  # "before" was served from the recording, not re-run
    assert len(store.list_parks("main")) == 1


async def test_a_rejection_fires_the_rejected_port_with_the_note() -> None:
    ran.clear()
    store = MemoryStore()
    reg = registry()
    first = await run_pipeline(registry=reg, config=pipeline(WIRED), store=store)
    [park] = store.list_parks("main")
    store.decide_park(park.id, "rejected", decided_by="me", decided_at=5, notes="tests missing")
    recording = load_recording(store, first.run_id)
    second = await run_pipeline(
        registry=reg,
        config=pipeline(WIRED),
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
        lineage=recording.lineage,
    )
    assert ran == ["before", "fix"]
    assert second.status == "done"
    routed = [s for s in store.list_steps(second.run_id) if s.node_path == "review"]
    assert routed[0].port == "rejected"
    assert routed[0].output["rejection"]["note"] == "tests missing"


async def test_a_watching_gate_waits_for_a_decision() -> None:
    ran.clear()
    store = MemoryStore()
    config = pipeline(GATE, header="on_idle: watch\n")

    async def approve_soon() -> None:
        for _ in range(500):
            parks = store.list_parks("main")
            if parks:
                store.decide_park(
                    parks[0].id, "approved", decided_by="me", decided_at=1, notes="ok"
                )
                return
            await asyncio.sleep(0.005)

    waiter = asyncio.ensure_future(approve_soon())
    result = await asyncio.wait_for(
        run_pipeline(registry=registry(), config=config, store=store), 10
    )
    await waiter
    assert result.status == "done" and ran == ["before", "after"]
    assert store.list_events(result.run_id, type="park.created")


async def test_a_watching_gate_stops_when_cancelled() -> None:
    from whiletrue.abort import AbortSignal

    signal = AbortSignal()
    config = pipeline(GATE, header="on_idle: watch\n")
    task = asyncio.ensure_future(
        run_pipeline(registry=registry(), config=config, store=MemoryStore(), signal=signal)
    )
    await asyncio.sleep(0.1)
    signal.abort()
    result = await asyncio.wait_for(task, 5)
    assert result.status == "cancelled"


async def test_a_file_wake_condition_approves_by_itself(tmp_path: Path) -> None:
    ran.clear()
    store = MemoryStore()
    (tmp_path / "signed-off").write_text("ok")
    body = """  - id: g
    human.approve: { wake: { any: [cli, { file_exists: signed-off }] } }
  - mark: { text: after }
"""
    result = await run_pipeline(
        registry=registry(), config=pipeline(body), store=store, cwd=str(tmp_path)
    )
    assert result.status == "done" and ran == ["after"]
    [park] = store.list_parks("main")
    assert (park.decision, park.decided_by) == ("approved", "file_exists")
    assert store.list_events(result.run_id, type="park.decided")[0].data["by"] == "file_exists"


async def test_a_timer_wake_condition_fires_after_the_wait() -> None:
    store = MemoryStore()
    clock = [1_000]
    body = "  - id: g\n    human.approve: { wake: { any: [{ timer: 1m }] } }\n"
    reg = registry()
    first = await run_pipeline(
        registry=reg, config=pipeline(body), store=store, now=lambda: clock[0]
    )
    assert first.status == "parked"
    clock[0] += 61_000
    recording = load_recording(store, first.run_id)
    second = await run_pipeline(
        registry=reg,
        config=pipeline(body),
        store=store,
        now=lambda: clock[0],
        items=recording.items,
        replay=recording.steps,
        resume=True,
        lineage=recording.lineage,
    )
    assert second.status == "done"


async def test_each_pass_through_a_gate_uses_its_own_park() -> None:
    ran.clear()
    store = MemoryStore()
    reg = registry()
    body = """  - id: loop
    loop:
      max: 3
      until: "iter.index >= 2"
    do:
      - id: g
        human.approve: { what: round }
"""
    first = await run_pipeline(registry=reg, config=pipeline(body), store=store)
    [p1] = store.list_parks("main")
    store.decide_park(p1.id, "approved", decided_by="me", decided_at=1, notes=None)
    recording = load_recording(store, first.run_id)
    second = await run_pipeline(
        registry=reg,
        config=pipeline(body),
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
        lineage=recording.lineage,
    )
    assert second.status == "parked"
    assert len(store.list_parks("main")) == 2  # the second pass created a new park


COMMENT_GATE = """  - id: g
    human.approve: { what: pr, poll: 10ms }
    do:
      - answer_all: {}
      - comments.reply: {}
      - gate.comments_answered: {}
"""


async def test_new_comments_get_answered_while_the_gate_stays_parked() -> None:
    comments = FakeComments()
    comments.incoming = [IncomingComment("c1", "alice", "why a global?", 5, thread="t1")]
    store = MemoryStore()
    config = pipeline(COMMENT_GATE, header="on_idle: watch\n")

    async def approve_after_reply() -> None:
        for _ in range(1000):
            if comments.replies and (parks := store.list_parks("main")):
                store.decide_park(
                    parks[0].id, "approved", decided_by="me", decided_at=1, notes=None
                )
                return
            await asyncio.sleep(0.005)

    waiter = asyncio.ensure_future(approve_after_reply())
    result = await asyncio.wait_for(
        run_pipeline(registry=registry(comments), config=config, store=store), 10
    )
    await waiter
    assert result.status == "done"
    assert comments.replies == [("c1", "[whiletrue] because")]
    [stored] = store.list_comments("main")
    assert stored.answered and stored.reply == "[whiletrue] because"
    assert store.list_events(result.run_id, type="comment.answered")


async def test_comments_are_answered_once_across_resumes() -> None:
    comments = FakeComments()
    comments.incoming = [IncomingComment("c1", "alice", "why?", 5)]
    store = MemoryStore()
    reg = registry(comments)
    first = await run_pipeline(registry=reg, config=pipeline(COMMENT_GATE), store=store)
    assert first.status == "parked" and len(comments.replies) == 1
    recording = load_recording(store, first.run_id)
    await run_pipeline(
        registry=reg,
        config=pipeline(COMMENT_GATE),
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
        lineage=recording.lineage,
    )
    assert len(comments.replies) == 1  # the comment is already answered and the effect is journaled


async def test_the_gate_without_children_just_waits() -> None:
    comments = FakeComments()
    comments.incoming = [IncomingComment("c1", "alice", "why?", 5)]
    result = await run_pipeline(
        registry=registry(comments),
        config=pipeline("  - human.approve: {}\n"),
        store=MemoryStore(),
    )
    assert result.status == "parked" and comments.replies == []


# ---- comments.reply, gate.comments_answered ------------------------------------------------


def step_ctx(store: MemoryStore, reg: Registry, **over: Any) -> StepContext[Any]:
    from whiletrue.core import EmptyConfig, create_step_context

    return create_step_context(EmptyConfig(), store=store, registry=reg, item_id="7", **over)


def seed(store: MemoryStore) -> int:
    store.create_run(id="r", pipeline_hash="h", started_at=1)
    record = store.add_comment(
        item_id="7", source="github_pr", external_id="c1", author="a", body="b", created_at=1
    )
    assert record is not None
    return record.id


async def test_comments_reply_posts_once_and_records_the_answer() -> None:
    from whiletrue.basics import comments_reply_module

    comments, store = FakeComments(), MemoryStore()
    cid = seed(store)
    ctx = step_ctx(store, registry(comments))
    payload = {"replies": [{"comment_id": cid, "kind": "nit", "reply": "[whiletrue] done"}]}
    result = await comments_reply_module.run({"in": payload}, ctx)
    assert (result.port, result.value) == ("ok", {"answered": [cid]})
    assert comments.replies == [("c1", "[whiletrue] done")]  # prefix is not doubled
    again = await comments_reply_module.run({"in": payload}, ctx)
    assert again.value == {"answered": []} and len(comments.replies) == 1


async def test_comments_reply_skips_posting_when_a_started_effect_exists() -> None:
    from whiletrue.basics import comments_reply_module

    comments, store = FakeComments(), MemoryStore()
    cid = seed(store)
    store.effect_put("reply:7:github_pr:c1", run_id="r", status="started", ref=None, created_at=1)
    payload = {"replies": [{"comment_id": cid, "reply": "x"}]}
    await comments_reply_module.run({"in": payload}, step_ctx(store, registry(comments)))
    assert comments.replies == []
    assert store.list_comments("7")[0].answered


async def test_comments_reply_records_replies_for_comments_without_a_provider() -> None:
    from whiletrue.basics import comments_reply_module

    store = MemoryStore()
    cid = seed(store)
    result = await comments_reply_module.run(
        {"in": {"replies": [{"comment_id": cid, "reply": "ok"}]}}, step_ctx(store, registry())
    )
    assert result.port == "ok" and store.list_comments("7")[0].reply == "[whiletrue] ok"


async def test_comments_reply_rejects_unknown_comments_and_needs_a_store() -> None:
    from whiletrue.basics import comments_reply_module

    store = MemoryStore()
    seed(store)
    bad = await comments_reply_module.run(
        {"in": {"replies": [{"comment_id": 99, "reply": "x"}]}}, step_ctx(store, registry())
    )
    assert bad.port == "fail" and "No comment 99" in bad.value["error"]
    from whiletrue.core import EmptyConfig, create_step_context

    nostore = create_step_context(EmptyConfig())
    assert (await comments_reply_module.run({"in": {}}, nostore)).port == "fail"


async def test_gate_comments_answered_lists_what_is_unanswered() -> None:
    from whiletrue.basics import gate_comments_answered_module

    store = MemoryStore()
    cid = seed(store)
    ctx = step_ctx(store, registry())
    failed = await gate_comments_answered_module.run({"in": "x"}, ctx)
    assert failed.port == "fail" and "#1 (a)" in failed.value["error"]
    store.answer_comment(cid, "done")
    passed = await gate_comments_answered_module.run({"in": "x"}, ctx)
    assert (passed.port, passed.value) == ("ok", "x")
    from whiletrue.core import EmptyConfig, create_step_context

    nostore = create_step_context(EmptyConfig())
    assert (await gate_comments_answered_module.run({}, nostore)).port == "fail"


# ---- validation ----------------------------------------------------------------------------


def issues(config_text: str) -> list[str]:
    config = parse_pipeline(config_text, "t.yaml")
    from whiletrue.core import has_errors

    diagnostics = validate_pipeline(config, registry(), cwd=".")
    del has_errors
    return [d.message for d in diagnostics]


def test_validate_rejects_unknown_wake_conditions_and_bad_specs() -> None:
    assert any(
        'Unknown wake condition "nope"' in m
        for m in issues("pipeline:\n  - human.approve: { wake: { any: [nope] } }\n")
    )
    assert any("at least one" in m for m in issues("pipeline:\n  - human.approve: { wake: [] }\n"))
    assert any("poll" in m for m in issues("pipeline:\n  - human.approve: { poll: soon }\n"))
    assert issues("pipeline:\n  - human.approve: { wake: { any: [cli, timer] } }\n") == []


def test_the_wake_providers_exist() -> None:
    r = registry()
    assert r.get_provider("wake", "file_exists") is not None
    assert r.get_provider("wake", "timer") is not None
