from collections.abc import Iterator
from pathlib import Path

import pytest

from whiletrue.core import ItemRecord, RunRecord, WtEvent
from whiletrue.store_sqlite import SqliteStore


@pytest.fixture
def store() -> Iterator[SqliteStore]:
    s = SqliteStore(":memory:")
    s.create_run(id="r1", pipeline_hash="h", started_at=100)
    yield s
    s.close()


def test_creates_every_documented_table_via_migrations(store: SqliteStore) -> None:
    assert store.schema_version() == 1
    assert set(store.tables()) >= {
        "runs",
        "items",
        "steps",
        "parks",
        "sessions",
        "effects",
        "learnings",
        "events",
        "cache",
        "schema_migrations",
    }


def test_persists_across_reopen_and_does_not_rerun_migrations(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.db"
    a = SqliteStore(path)
    a.create_run(id="x", pipeline_hash="h", lockfile="lock", started_at=1)
    a.close()
    b = SqliteStore(path)
    assert b.schema_version() == 1
    run = b.get_run("x")
    assert run is not None
    assert (run.id, run.lockfile, run.status) == ("x", "lock", "running")
    b.close()


def test_creates_finishes_and_fetches_runs(store: SqliteStore) -> None:
    assert store.get_run("r1") == RunRecord(
        id="r1", pipeline_hash="h", lockfile=None, status="running", started_at=100, ended_at=None
    )
    store.finish_run("r1", "done", 200)
    run = store.get_run("r1")
    assert run is not None
    assert (run.status, run.ended_at) == ("done", 200)
    assert store.get_run("nope") is None


def test_returns_the_latest_run(store: SqliteStore) -> None:
    store.create_run(id="r2", pipeline_hash="h", started_at=300)
    latest = store.latest_run()
    assert latest is not None
    assert latest.id == "r2"


def test_latest_run_is_none_when_empty() -> None:
    empty = SqliteStore(":memory:")
    assert empty.latest_run() is None
    empty.close()


def test_upserts_items_updates_state_and_lists(store: SqliteStore) -> None:
    store.upsert_item(
        ItemRecord(
            id="i1",
            run_id="r1",
            source_key="k",
            state="ready",
            payload={"n": 1},
            depends_on=["i0"],
        )
    )
    store.upsert_item(
        ItemRecord(id="i1", run_id="r1", source_key="k2", state="running", payload=None)
    )
    store.set_item_state("r1", "i1", "done")
    assert store.list_items("r1") == [
        ItemRecord(id="i1", run_id="r1", source_key="k2", state="done", payload=None, depends_on=[])
    ]


def test_rejects_state_changes_for_unknown_items(store: SqliteStore) -> None:
    with pytest.raises(KeyError, match="No item"):
        store.set_item_state("r1", "ghost", "done")


def test_records_a_step_lifecycle(store: SqliteStore) -> None:
    step_id = store.start_step(
        run_id="r1", item_id="i1", node_path="root.0", attempt=1, input={"a": 1}, started_at=10
    )
    first = store.list_steps("r1")[0]
    assert (first.id, first.status, first.port, first.input) == (step_id, "running", None, {"a": 1})
    store.finish_step(step_id, status="completed", port="ok", output="x", cost_usd=0.5, ended_at=20)
    done = store.list_steps("r1", "i1")[0]
    assert (done.status, done.port, done.output, done.cost_usd, done.error, done.ended_at) == (
        "completed",
        "ok",
        "x",
        0.5,
        None,
        20,
    )
    assert store.list_steps("r1", "other") == []


def test_records_errors_with_defaults(store: SqliteStore) -> None:
    step_id = store.start_step(
        run_id="r1", item_id="i1", node_path="p", attempt=2, input=None, started_at=1
    )
    store.finish_step(step_id, status="errored", error="boom", ended_at=2)
    step = store.list_steps("r1")[0]
    assert (step.status, step.error, step.cost_usd, step.port, step.output, step.attempt) == (
        "errored",
        "boom",
        0,
        None,
        None,
        2,
    )


def test_rejects_finishing_an_unknown_step(store: SqliteStore) -> None:
    with pytest.raises(KeyError, match="No step"):
        store.finish_step(999, status="completed", ended_at=1)


def test_appends_and_filters_events_in_order(store: SqliteStore) -> None:
    store.append_event(WtEvent(type="run.started", run_id="r1", ts=1))
    store.append_event(
        WtEvent(type="step.started", run_id="r1", item_id="i1", step_id=3, ts=2, data={"a": 1})
    )
    store.append_event(WtEvent(type="log", run_id="r1", item_id="i2", ts=3, data={"m": "x"}))
    assert [e.type for e in store.list_events("r1")] == ["run.started", "step.started", "log"]
    assert store.list_events("r1", item_id="i1") == [
        WtEvent(type="step.started", run_id="r1", item_id="i1", step_id=3, ts=2, data={"a": 1})
    ]
    assert len(store.list_events("r1", type="log")) == 1
    assert len(store.list_events("r1", item_id="i2", type="log")) == 1
    assert store.list_events("other") == []


def test_migration_failures_roll_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from whiletrue.store_sqlite import sqlite_store

    monkeypatch.setattr(sqlite_store, "MIGRATIONS", ("CREATE TABLE a (x);", "THIS IS NOT SQL;"))
    with pytest.raises(Exception, match="syntax error"):
        SqliteStore(":memory:")
