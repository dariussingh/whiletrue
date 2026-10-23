"""Operating a project's runs: the run lock, crash recovery, rerun, cancel, logs and cost."""

from __future__ import annotations

import asyncio
import fcntl
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from whiletrue.cli.common import EXIT, Io
from whiletrue.core import (
    ReplayError,
    RunItem,
    SourceError,
    fetch_items,
    find_dependency_cycles,
    load_recording,
    run_pipeline,
)
from whiletrue.store_sqlite import SqliteStore

if TYPE_CHECKING:
    from whiletrue.cli.app import _Loaded  # pyright: ignore[reportPrivateUsage]


class RunLock:
    """An exclusive lock on `.whiletrue/run.lock`, held for as long as a run's process lives.

    It tells a crashed run (status `running`, nobody holds the lock) from a live one.
    """

    def __init__(self, cwd: str) -> None:
        self._path = Path(cwd) / ".whiletrue" / "run.lock"
        self._handle: Any = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None

    def holder(self) -> str:
        try:
            return self._path.read_text().strip() or "?"
        except OSError:
            return "?"


def _status_code(status: str) -> int:
    return {"done": EXIT.ok, "parked": EXIT.parked, "cancelled": EXIT.cancelled}.get(
        status, EXIT.failed
    )


def _log(io: Io):
    def log(level: str, message: str, data: object = None) -> None:
        if level != "debug":
            io.out(f"[{level}] {message}\n")

    return log


async def source_items(loaded: _Loaded) -> list[RunItem] | None:
    return await fetch_items(loaded.registry, loaded.config.extra, loaded.cwd)


def cycle_error(items: list[RunItem] | None) -> str | None:
    cycles = find_dependency_cycles(items or [])
    if not cycles:
        return None
    return "Items depend on each other in a cycle: " + "; ".join(" -> ".join(c) for c in cycles)


async def resume(io: Io, loaded: _Loaded, run_id: str | None, lock_hash: str | None) -> int:
    """Continue a parked run, or one whose process died: recorded steps are replayed."""
    db_path = Path(loaded.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        if run_id is None:
            latest = store.latest_run()
            run_id = latest.id if latest else ""
        run = store.get_run(run_id)
        if run is not None and run.status == "running":
            # We hold the run lock, so nothing is running it: the process died.
            store.interrupt_run(run_id, int(time.time() * 1000))
            io.out(f"run {run_id} was interrupted (its process is gone); recovering it\n")
        try:
            recording = load_recording(store, run_id)
        except ReplayError as error:
            io.err(f"{error}\n")
            return EXIT.failed
        if recording.status != "parked":
            io.err(f"Run {run_id} is {recording.status}, not parked: nothing to resume\n")
            return EXIT.failed
        items = [i for i in recording.items if recording.item_states.get(i.id) != "cancelled"]
        try:
            fresh = await source_items(loaded)
        except SourceError as error:
            io.err(f"Cannot fetch items: {error}\n")
            return EXIT.failed
        if fresh is not None:
            # The source wins: items it no longer lists are dropped, and its newest data is used.
            wanted = {i.id for i in items}
            gone = sorted(wanted - {f.id for f in fresh})
            if gone:
                io.out(f"no longer in the source, dropped: {', '.join(gone)}\n")
            items = [f for f in fresh if f.id in wanted or f.id not in recording.item_states]
        result = await run_pipeline(
            registry=loaded.registry,
            config=loaded.config,
            store=store,
            cwd=loaded.cwd,
            signal=io.signal,
            log=_log(io),
            lockfile=lock_hash,
            items=items,
            replay=recording.steps,
            resume=True,
            lineage=recording.lineage,
        )
        io.out(f"resumed {recording.run_id} as run {result.run_id}: {result.status}\n")
        for item in result.items:
            io.out(f"  {item.item_id}: {item.state} ({item.port})\n")
        return _status_code(result.status)
    finally:
        store.close()


async def rerun(
    io: Io, loaded: _Loaded, item_id: str, from_step: str | None, lock_hash: str | None
) -> int:
    """Run one item again as a new run; with `--from`, steps before that step are replayed."""
    db_path = Path(loaded.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        latest = store.latest_run()
        if latest is None:
            io.err("No runs yet\n")
            return EXIT.failed
        if latest.status == "running":
            io.err("The latest run is still running; cancel it or wait\n")
            return EXIT.failed
        recording = load_recording(store, latest.id)
        item = next((i for i in recording.items if i.id == item_id), None)
        if item is None:
            io.err(f'Run {latest.id} has no item "{item_id}"\n')
            return EXIT.failed
        replay: dict[tuple[str, str, int], tuple[str, Any]] = {}
        if from_step is not None:
            steps = store.list_steps(latest.id, item_id)
            cut = next((s.id for s in steps if s.node_path == from_step), None)
            if cut is None:
                io.err(f'Item "{item_id}" has no step "{from_step}" in run {latest.id}\n')
                return EXIT.failed
            replay = {
                (s.item_id, s.node_path, s.attempt): (s.port, s.output)
                for s in steps
                if s.id < cut and s.port is not None and s.port != "needs_human"
            }
        # Its blockers already ran; only this item is run again.
        again = RunItem(item.id, item.payload, [])
        result = await run_pipeline(
            registry=loaded.registry,
            config=loaded.config,
            store=store,
            cwd=loaded.cwd,
            signal=io.signal,
            log=_log(io),
            lockfile=lock_hash,
            items=[again],
            replay=replay if from_step is not None else None,
            resume=True,
        )
        io.out(f"rerun of {item_id} as run {result.run_id}: {result.status}\n")
        return _status_code(result.status)
    finally:
        store.close()


def cancel(io: Io, item_id: str | None) -> int:
    """Cancel an item in the latest run. A running one stops before its next step."""
    if item_id is None:
        io.err("Usage: whiletrue cancel <item>\n")
        return EXIT.usage
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        run = store.latest_run()
        record = store.get_item(run.id, item_id) if run else None
        if run is None or record is None:
            io.err(f'No item "{item_id}" in the latest run\n')
            return EXIT.failed
        if record.state in ("done", "cancelled"):
            io.err(f'"{item_id}" is already {record.state}\n')
            return EXIT.failed
        for park in store.list_parks(item_id, undecided_only=True):
            store.decide_park(
                park.id, "rejected", decided_by="cancel", decided_at=_now(), notes="cancelled"
            )
        store.set_item_state(run.id, item_id, "cancelled")
        io.out(f"{item_id}: cancelled\n")
        return EXIT.ok
    finally:
        store.close()


def _now() -> int:
    return int(time.time() * 1000)


def _event_line(event: Any) -> str:
    stamp = time.strftime("%H:%M:%S", time.gmtime(event.ts / 1000))
    detail = " ".join(f"{k}={v}" for k, v in event.data.items() if k not in ("files",))
    return f"{stamp}  {event.type:<16} {detail}".rstrip()


async def logs(io: Io, item_id: str | None, follow: bool) -> int:
    if item_id is None:
        io.err("Usage: whiletrue logs <item> [--follow]\n")
        return EXIT.usage
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        run = store.latest_run()
        if run is None:
            io.err("No runs yet\n")
            return EXIT.failed
        shown = 0
        while True:
            events = store.list_events(run.id, item_id=item_id)
            for event in events[shown:]:
                io.out(_event_line(event) + "\n")
            shown = len(events)
            current = store.get_run(run.id)
            if not follow or current is None or current.status != "running":
                break
            if io.signal is not None and io.signal.aborted:
                return EXIT.cancelled
            await asyncio.sleep(0.5)
        if shown == 0:
            io.err(f'No events for "{item_id}" in run {run.id}\n')
            return EXIT.failed
        return EXIT.ok
    finally:
        store.close()


def cost(io: Io, run_id: str | None) -> int:
    """Spend by item, by step and by runner."""
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return EXIT.failed
    store = SqliteStore(db_path)
    try:
        run = store.get_run(run_id) if run_id else store.latest_run()
        if run is None:
            io.err("No runs yet\n")
            return EXIT.failed
        runner_of: dict[tuple[str, str], str] = {}
        for event in store.list_events(run.id, type="agent.event"):
            if event.data.get("kind") == "runner" and event.item_id:
                runner_of[(event.item_id, str(event.data.get("node")))] = str(event.data["runner"])
        by_item: dict[str, float] = defaultdict(float)
        by_step: dict[str, float] = defaultdict(float)
        by_runner: dict[str, float] = defaultdict(float)
        for step in store.list_steps(run.id):
            if step.cost_usd <= 0:
                continue
            by_item[step.item_id] += step.cost_usd
            by_step[step.node_path] += step.cost_usd
            by_runner[runner_of.get((step.item_id, step.node_path), "other")] += step.cost_usd
        total = sum(by_item.values())
        io.out(f"run {run.id}  total ${total:.4f}\n")
        for title, table in (("by item", by_item), ("by step", by_step), ("by runner", by_runner)):
            io.out(f"{title}:\n")
            for name, usd in sorted(table.items(), key=lambda kv: -kv[1]):
                io.out(f"  {name:<24} ${usd:.4f}\n")
        return EXIT.ok
    finally:
        store.close()
