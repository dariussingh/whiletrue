import pytest

from whiletrue.core import ItemRecord, MemoryStore, WtEvent


def test_runs_items_steps_and_events_round_trip() -> None:
    s = MemoryStore()
    assert s.latest_run() is None
    s.create_run(id="r", pipeline_hash="h", started_at=1, lockfile="l")
    s.create_run(id="r2", pipeline_hash="h", started_at=2)
    latest = s.latest_run()
    assert latest is not None
    assert latest.id == "r2"
    s.finish_run("r", "done", 5)
    run = s.get_run("r")
    assert run is not None
    assert (run.status, run.ended_at, run.lockfile) == ("done", 5, "l")
    assert s.get_run("zzz") is None

    s.upsert_item(ItemRecord(id="i", run_id="r", source_key="i", state="ready", depends_on=["a"]))
    s.upsert_item(ItemRecord(id="i", run_id="r", source_key="i", state="running"))
    s.upsert_item(ItemRecord(id="j", run_id="r", source_key="j", state="ready"))
    s.set_item_state("r", "i", "done")
    assert [(i.id, i.state) for i in s.list_items("r")] == [("i", "done"), ("j", "ready")]

    a = s.start_step(run_id="r", item_id="i", node_path="n", attempt=1, input=1, started_at=1)
    b = s.start_step(run_id="r", item_id="j", node_path="n", attempt=1, input=2, started_at=1)
    s.finish_step(a, status="completed", port="ok", output="o", cost_usd=1, ended_at=2)
    assert [x.id for x in s.list_steps("r")] == [a, b]
    assert [x.id for x in s.list_steps("r", "j")] == [b]
    assert s.list_steps("r", "i")[0].output == "o"

    s.append_event(WtEvent(type="log", run_id="r", ts=1, item_id="i"))
    s.append_event(WtEvent(type="run.started", run_id="r", ts=2))
    assert len(s.list_events("r")) == 2
    assert len(s.list_events("r", item_id="i")) == 1
    assert len(s.list_events("r", type="run.started")) == 1
    s.close()


def test_raises_for_unknown_ids() -> None:
    s = MemoryStore()
    with pytest.raises(KeyError):
        s.finish_run("nope", "done", 1)
    with pytest.raises(KeyError):
        s.set_item_state("r", "i", "done")
    with pytest.raises(KeyError):
        s.finish_step(9, status="completed", ended_at=1)
