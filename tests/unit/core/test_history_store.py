"""Parks, comments, steers, effects and learnings behave the same in every StateStore."""

from collections.abc import Iterator
from typing import Literal

import pytest

from whiletrue.core import CommentRecord, MemoryStore, StateStore
from whiletrue.store_sqlite import SqliteStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest) -> Iterator[StateStore]:
    s: StateStore = MemoryStore() if request.param == "memory" else SqliteStore(":memory:")
    s.create_run(id="r1", pipeline_hash="h", started_at=1)
    yield s
    s.close()


def park(store: StateStore, item: str = "7", node: str = "gate", created: int = 10):
    return store.create_park(
        run_id="r1",
        item_id=item,
        node_path=node,
        what="pr",
        wake={"any": ["cli"]},
        reason="waiting for review",
        created_at=created,
    )


def test_a_park_records_its_decision(store: StateStore) -> None:
    created = park(store)
    assert (created.what, created.wake, created.decision) == ("pr", {"any": ["cli"]}, None)
    store.decide_park(created.id, "rejected", decided_by="me", decided_at=20, notes="fix tests")
    decided = store.list_parks("7")[0]
    assert (decided.decision, decided.decided_by, decided.decided_at, decided.notes) == (
        "rejected",
        "me",
        20,
        "fix tests",
    )
    with pytest.raises(KeyError):
        store.decide_park(99, "approved", decided_by="me", decided_at=1, notes=None)


def test_parks_list_in_creation_order_and_filter(store: StateStore) -> None:
    first = park(store, created=1)
    store.decide_park(first.id, "approved", decided_by="me", decided_at=2, notes=None)
    second = park(store, created=3)
    park(store, item="8")
    park(store, node="other")
    assert [p.id for p in store.list_parks("7", node_path="gate")] == [first.id, second.id]
    assert len(store.list_parks()) == 4
    assert [p.item_id for p in store.list_parks(undecided_only=True)] == ["7", "8", "7"]
    assert store.list_parks("7", run_id="nope") == []
    assert len(store.list_parks(run_id="r1")) == 4


def comment(
    store: StateStore,
    external_id: str,
    body: str,
    *,
    source: str = "github_pr",
    kind: Literal["human", "reviewer"] = "human",
    thread: str | None = None,
    url: str | None = None,
) -> CommentRecord | None:
    return store.add_comment(
        item_id="7",
        source=source,
        external_id=external_id,
        author="alice",
        body=body,
        created_at=5,
        thread=thread,
        url=url,
        kind=kind,
    )


def test_comments_are_deduplicated_and_answerable(store: StateStore) -> None:
    first = comment(store, "c1", "why this?", thread="t1", url="u")
    assert first is not None
    assert (first.thread, first.url, first.kind, first.answered) == ("t1", "u", "human", False)
    assert comment(store, "c1", "dup") is None
    assert comment(store, "c1", "same id, other source", source="local")
    comment(store, "c2", "bot", kind="reviewer")

    assert len(store.list_comments("7")) == 3
    store.answer_comment(first.id, "because")
    unanswered = store.list_comments("7", unanswered_only=True)
    assert [c.external_id for c in unanswered] == ["c1", "c2"]
    answered = store.list_comments("7")[0]
    assert (answered.answered, answered.reply) == (True, "because")
    assert store.list_comments("nope") == []
    with pytest.raises(KeyError):
        store.answer_comment(999, "x")


def test_steers_are_recorded_per_item(store: StateStore) -> None:
    store.add_steer(run_id="r1", item_id="7", step="impl", prompt="use sqlite", created_at=3)
    store.add_steer(run_id="r1", item_id="8", step="impl", prompt="other", created_at=4)
    steers = store.list_steers("7")
    assert [(s.step, s.prompt) for s in steers] == [("impl", "use sqlite")]


def test_effects_are_journaled_by_key(store: StateStore) -> None:
    assert store.effect_get("k") is None
    store.effect_put("k", run_id="r1", status="started", ref=None, created_at=1)
    started = store.effect_get("k")
    assert started is not None and (started.status, started.ref) == ("started", None)
    store.effect_put("k", run_id="r1", status="done", ref={"url": "u"}, created_at=2)
    done = store.effect_get("k")
    assert done is not None and (done.status, done.ref, done.run_id) == ("done", {"url": "u"}, "r1")


def test_learnings_have_a_review_status(store: StateStore) -> None:
    a = store.add_learning(
        scope="project", note="use uv", status="proposed", created_at=1, provenance={"item": "7"}
    )
    store.add_learning(scope="run", note="tmp", status="approved", created_at=2, run_id="r1")
    assert (a.status, a.provenance) == ("proposed", {"item": "7"})
    assert [x.note for x in store.list_learnings(status="proposed")] == ["use uv"]
    assert [x.note for x in store.list_learnings(scope="run")] == ["tmp"]
    store.set_learning_status(a.id, "approved")
    assert len(store.list_learnings(status="approved")) == 2
    with pytest.raises(KeyError):
        store.set_learning_status(99, "rejected")


def test_interrupting_a_run_parks_what_was_in_flight(store: StateStore) -> None:
    from whiletrue.core import ItemRecord

    store.upsert_item(ItemRecord("a", "r1", "a", "running"))
    store.upsert_item(ItemRecord("b", "r1", "b", "done"))
    running = store.start_step(
        run_id="r1", item_id="a", node_path="n", attempt=1, input=None, started_at=1
    )
    finished = store.start_step(
        run_id="r1", item_id="b", node_path="n", attempt=1, input=None, started_at=1
    )
    store.finish_step(finished, status="completed", port="ok", ended_at=2)
    store.interrupt_run("r1", 50)
    steps = {s.id: s for s in store.list_steps("r1")}
    assert (steps[running].status, steps[running].error, steps[running].ended_at) == (
        "errored",
        "interrupted",
        50,
    )
    assert steps[finished].status == "completed"
    assert {i.id: i.state for i in store.list_items("r1")} == {"a": "parked", "b": "done"}
    run = store.get_run("r1")
    assert run is not None and (run.status, run.ended_at) == ("parked", 50)
