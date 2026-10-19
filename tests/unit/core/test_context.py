import dataclasses

import pytest

from whiletrue.core import Resource, Scope, create_step_context


def test_inherits_values_from_the_parent_and_shadows_them() -> None:
    root = Scope()
    root.set("repo", "r")
    root.set("item", "a")
    child = root.child()
    child.set("item", "b")
    assert child.get("repo") == "r"
    assert child.get("item") == "b"
    assert root.get("item") == "a"
    assert child.has("repo")
    assert not child.has("zzz")
    assert child.get("zzz") is None
    assert child.snapshot() == {"repo": "r", "item": "b"}


def test_root_scope_has_an_empty_snapshot() -> None:
    s = Scope()
    assert s.snapshot() == {}
    assert not s.has("x")
    assert s.get("x") is None


async def test_disposes_resources_in_reverse_order_once() -> None:
    order: list[str] = []

    async def dispose_session() -> None:
        order.append("session")

    s = Scope()
    s.add_resource(Resource("worktree", lambda: order.append("worktree")))
    s.add_resource(Resource("session", dispose_session))
    assert s.resource_names == ["worktree", "session"]
    await s.dispose()
    await s.dispose()
    assert order == ["session", "worktree"]


async def test_runs_every_disposer_and_raises_the_first_failure() -> None:
    order: list[str] = []

    def fail(message: str) -> None:
        raise RuntimeError(message)

    s = Scope()
    s.add_resource(Resource("a", lambda: order.append("a")))
    s.add_resource(Resource("b", lambda: fail("first")))
    s.add_resource(Resource("c", lambda: fail("second")))
    with pytest.raises(RuntimeError, match="second"):
        await s.dispose()
    assert order == ["a"]


async def test_refuses_new_resources_after_disposal() -> None:
    s = Scope()
    await s.dispose()
    with pytest.raises(RuntimeError, match="already disposed"):
        s.add_resource(Resource("late", lambda: None))


def test_create_step_context_fills_defaults() -> None:
    ctx = create_step_context({"a": 1})
    assert ctx.config == {"a": 1}
    assert ctx.step_key == "run:item:node"
    assert ctx.attempt == 1
    assert not ctx.signal.aborted
    ctx.log("info", "x")
    ctx.record_cost(1.0)
    assert ctx.registry.caps.has("Text")


def test_create_step_context_accepts_overrides() -> None:
    ctx = create_step_context({}, run_id="r1", item_id="i1", node_id="n1", cwd="/tmp")
    assert ctx.step_key == "r1:i1:n1"
    assert ctx.cwd == "/tmp"
    assert dataclasses.replace(ctx, attempt=3).attempt == 3
