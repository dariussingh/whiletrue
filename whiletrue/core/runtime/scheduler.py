"""Running a pipeline: items are tokens that wait for their dependencies and for a free slot."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

from whiletrue.abort import AbortSignal
from whiletrue.core.config import PipelineConfig
from whiletrue.core.context import Locks, Logger
from whiletrue.core.events import EventBus
from whiletrue.core.lock import collect_file_hashes
from whiletrue.core.outcomes import OutcomePolicy
from whiletrue.core.registry import Registry
from whiletrue.core.runtime.env import RunEnv
from whiletrue.core.runtime.items import ItemRunner
from whiletrue.core.runtime.model import (
    ItemOutcome,
    RunItem,
    RunResult,
    find_dependency_cycles,
    hash_pipeline,
    run_status,
)
from whiletrue.core.store import ItemRecord, StateStore


def _noop_log(level: str, message: str, data: dict[str, Any] | None = None) -> None:
    return None


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


class Scheduler:
    """Runs the items of one run: dependencies first, at most `slots` at a time."""

    def __init__(self, env: RunEnv, items: list[RunItem], slots: int) -> None:
        self._env = env
        self._items = items
        self._slots = asyncio.Semaphore(slots)
        self._cycle_ids = {i for cycle in find_dependency_cycles(items) for i in cycle}
        self._finished: dict[str, str] = {}
        self._results: dict[str, ItemOutcome] = {}
        self._done = {i.id: asyncio.Event() for i in items}

    def _block(self, item: RunItem, blocked_by: list[str]) -> None:
        env = self._env
        self._finished[item.id] = "pending"
        env.store.set_item_state(env.run_id, item.id, "pending")
        env.bus.emit(
            "item.state",
            env.run_id,
            {"state": "pending", "blocked_by": blocked_by},
            item_id=item.id,
        )
        self._results[item.id] = ItemOutcome(item.id, "pending", "skip", {"blocked_by": blocked_by})

    async def _work(self, item: RunItem) -> None:
        try:
            if item.id in self._cycle_ids:
                self._block(item, ["dependency cycle"])
                return
            for dep in item.depends_on:
                if dep in self._done:
                    await self._done[dep].wait()
            blocked_by = [d for d in item.depends_on if self._finished.get(d) != "done"]
            if blocked_by:
                self._block(item, blocked_by)
                return
            async with self._slots:
                outcome = await ItemRunner(self._env, item).run()
            self._finished[item.id] = outcome.state
            self._results[item.id] = outcome
        finally:
            self._done[item.id].set()

    async def run(self) -> list[ItemOutcome]:
        await asyncio.gather(*(self._work(i) for i in self._items))
        return [self._results[i.id] for i in self._items]


def _start_run(
    env: RunEnv, items: list[RunItem], pipeline_hash: str, lockfile: str | None
) -> Callable[[], None]:
    """Records the run and its items; returns what unsubscribes the event log."""
    env.store.create_run(
        id=env.run_id, pipeline_hash=pipeline_hash, started_at=env.now(), lockfile=lockfile
    )
    unsubscribe = env.bus.subscribe(env.store.append_event)
    env.bus.emit(
        "run.started",
        env.run_id,
        {"pipeline": env.config.name, "items": len(items), "lineage": env.lineage},
    )
    env.bus.emit(
        "run.hashes",
        env.run_id,
        {"pipeline": pipeline_hash, "files": collect_file_hashes(env.config, env.cwd)},
    )
    for item in items:
        env.store.upsert_item(
            ItemRecord(
                id=item.id,
                run_id=env.run_id,
                source_key=item.id,
                state="ready",
                payload=item.payload,
                depends_on=list(item.depends_on),
            )
        )
    return unsubscribe


async def run_pipeline(
    *,
    registry: Registry,
    config: PipelineConfig,
    store: StateStore,
    bus: EventBus | None = None,
    #: Defaults to a single implicit item named "main".
    items: list[RunItem] | None = None,
    cwd: str | None = None,
    signal: AbortSignal | None = None,
    run_id: str | None = None,
    log: Logger | None = None,
    now: Callable[[], int] = _wall_clock_ms,
    #: Recorded on the run (e.g. a hash of whiletrue.lock).
    lockfile: str | None = None,
    #: Serve recorded results instead of running modules with effects (see `core.replay`).
    replay: dict[tuple[str, str, int], tuple[str, Any]] | None = None,
    #: With `replay`: run steps that have no recording instead of failing (continue a parked run).
    resume: bool = False,
    #: Gates wait for decisions instead of parking; defaults to the pipeline's `on_idle: watch`.
    watch: bool | None = None,
    #: The first run of a chain of resumes; a gate's parks belong to it. Defaults to this run.
    lineage: str | None = None,
    #: How many items run at once; defaults to the pipeline's `parallel` (1).
    parallel: int | None = None,
) -> RunResult:
    """Runs each item through the module tree and persists every step.

    Items are tokens: up to `parallel` run at once, an item waits for the items it depends on, and
    a parked or waiting item holds no slot beyond its own while the others continue.
    """
    run_id = run_id if run_id is not None else str(uuid.uuid4())
    items = items if items is not None else [RunItem("main")]
    env = RunEnv(
        registry=registry,
        config=config,
        store=store,
        bus=bus if bus is not None else EventBus(),
        signal=signal if signal is not None else AbortSignal(),
        log=log if log is not None else _noop_log,
        now=now,
        policy=OutcomePolicy(on_fail=config.on_fail),
        run_id=run_id,
        cwd=cwd if cwd is not None else os.getcwd(),
        watching=config.on_idle == "watch" if watch is None else watch,
        lineage=lineage if lineage is not None else run_id,
        locks=Locks(),
        agent_slots=asyncio.Semaphore(config.max_agents) if config.max_agents else None,
        replay=replay,
        resume=resume,
    )
    unsubscribe = _start_run(env, items, hash_pipeline(config), lockfile)
    slots = max(1, parallel if parallel is not None else config.parallel)
    outcomes = await Scheduler(env, items, slots).run()
    status = run_status(outcomes)
    store.finish_run(run_id, status, now())
    env.bus.emit("run.finished", run_id, {"status": status})
    unsubscribe()
    return RunResult(run_id=run_id, status=status, items=outcomes)
