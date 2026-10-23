from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from whiletrue.abort import AbortSignal
from whiletrue.core.config import PipelineConfig, PipelineNode
from whiletrue.core.context import (
    CompositeContext,
    Locks,
    Logger,
    Scope,
    StepContext,
    create_step_context,
)
from whiletrue.core.events import EventBus
from whiletrue.core.lock import collect_file_hashes
from whiletrue.core.module import Emit, PortValues
from whiletrue.core.outcomes import (
    ItemState,
    OutcomePolicy,
    is_outcome_port,
    item_state_for,
    transition,
)
from whiletrue.core.registry import Registry
from whiletrue.core.store import ItemRecord, RunStatus, StateStore
from whiletrue.core.template import render_deep
from whiletrue.core.workspace import Workspace, resolve_workspace, workspace_spec


@dataclass
class RunItem:
    id: str
    payload: Any = None
    #: Ids of items that must be done first; the item waits (stays `pending`) until they are.
    depends_on: list[str] = field(default_factory=lambda: [])


@dataclass
class ItemOutcome:
    item_id: str
    state: ItemState
    port: str
    value: Any = None


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    items: list[ItemOutcome] = field(default_factory=lambda: [])


def hash_pipeline(config: PipelineConfig) -> str:
    def strip(node: PipelineNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "use": node.use,
            "config": node.config,
            "wires": node.wires,
            "children": [strip(c) for c in node.children],
        }

    payload = json.dumps(strip(config.root), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_key(module: Any, parsed: Any, input: PortValues) -> str | None:
    """Cache key for pure/deterministic modules that don't read the workspace; else None."""
    if module.effects not in ("pure", "deterministic") or not module.cacheable:
        return None
    payload = json.dumps(
        [module.id, parsed.model_dump(mode="json"), input], sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _noop_log(level: str, message: str, data: dict[str, Any] | None = None) -> None:
    return None


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


def _error_message(error: BaseException) -> str:
    return str(error)


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
    bus = bus if bus is not None else EventBus()
    run_id = run_id if run_id is not None else str(uuid.uuid4())
    cwd = cwd if cwd is not None else os.getcwd()
    signal = signal if signal is not None else AbortSignal()
    log = log if log is not None else _noop_log
    items = items if items is not None else [RunItem("main")]
    policy = OutcomePolicy(on_fail=config.on_fail)
    watching = config.on_idle == "watch" if watch is None else watch
    lineage = lineage if lineage is not None else run_id
    locks = Locks()
    slots = asyncio.Semaphore(max(1, parallel if parallel is not None else config.parallel))
    agent_slots = asyncio.Semaphore(config.max_agents) if config.max_agents else None

    pipeline_hash = hash_pipeline(config)
    store.create_run(id=run_id, pipeline_hash=pipeline_hash, started_at=now(), lockfile=lockfile)
    unsubscribe = bus.subscribe(store.append_event)
    bus.emit(
        "run.started", run_id, {"pipeline": config.name, "items": len(items), "lineage": lineage}
    )
    bus.emit(
        "run.hashes",
        run_id,
        {"pipeline": pipeline_hash, "files": collect_file_hashes(config, cwd)},
    )

    attempts: dict[str, int] = {}

    for item in items:
        store.upsert_item(
            ItemRecord(
                id=item.id,
                run_id=run_id,
                source_key=item.id,
                state="ready",
                payload=item.payload,
                depends_on=list(item.depends_on),
            )
        )

    finished: dict[str, ItemState] = {}
    cycle_ids = {i for cycle in find_dependency_cycles(items) for i in cycle}
    results: dict[str, ItemOutcome] = {}

    def block(item: RunItem, blocked_by: list[str]) -> None:
        finished[item.id] = "pending"
        store.set_item_state(run_id, item.id, "pending")
        bus.emit(
            "item.state", run_id, {"state": "pending", "blocked_by": blocked_by}, item_id=item.id
        )
        results[item.id] = ItemOutcome(item.id, "pending", "skip", {"blocked_by": blocked_by})

    async def run_item(item: RunItem) -> None:
        item_scope = Scope()
        item_scope.set("run", {"id": run_id})
        #: Agent session ids shared across this item's steps (`session: item` or a name).
        item_scope.set("sessions", {})
        raw_payload: Any = item.payload
        fields: dict[str, Any] = {}
        if isinstance(raw_payload, dict):
            fields = {str(k): v for k, v in cast(dict[Any, Any], raw_payload).items()}
        item_scope.set("item", {"id": item.id, **fields})

        current: list[ItemState] = ["ready"]
        cwd_cell = [cwd]
        workspace: Workspace | None = None

        def set_state(state: ItemState, item_id: str = item.id) -> None:
            current[0] = transition(current[0], state)
            store.set_item_state(run_id, item_id, state)
            bus.emit("item.state", run_id, {"state": state}, item_id=item_id)

        def cancelled_in_store(item_id: str = item.id) -> bool:
            record = store.get_item(run_id, item_id)
            return record is not None and record.state == "cancelled"

        async def execute(
            node: PipelineNode,
            input: PortValues,
            parent_scope: Scope,
            item_id: str = item.id,
        ) -> Emit:
            resolved = registry.resolve(node.use)
            key = f"{item_id}:{node.id}"
            attempt = attempts.get(key, 0) + 1
            attempts[key] = attempt

            step_id = store.start_step(
                run_id=run_id,
                item_id=item_id,
                node_path=node.id,
                attempt=attempt,
                input=input,
                started_at=now(),
            )
            bus.emit(
                "step.started",
                run_id,
                {"node": node.id, "use": node.use, "attempt": attempt},
                item_id=item_id,
                step_id=step_id,
            )

            cost = 0.0

            def record_cost(usd: float) -> None:
                nonlocal cost
                cost += usd

            def finish(emit: Emit, errored: str | None = None) -> Emit:
                store.finish_step(
                    step_id,
                    status="errored" if errored else "completed",
                    port=emit.port,
                    output=emit.value,
                    cost_usd=cost,
                    error=errored,
                    ended_at=now(),
                )
                data: dict[str, Any] = {"node": node.id, "port": emit.port, "cost_usd": cost}
                if errored:
                    data["error"] = errored
                bus.emit(
                    "step.error" if errored else "step.finished",
                    run_id,
                    data,
                    item_id=item_id,
                    step_id=step_id,
                )
                return emit

            def fail(message: str) -> Emit:
                return finish(Emit("fail", {"error": message}), message)

            if signal.aborted or cancelled_in_store():
                return fail("cancelled")
            if resolved is None:
                return fail(f'Unknown module "{node.use}"')

            scope = parent_scope.child()
            module = resolved.module
            try:
                rendered = render_deep(node.config, {**scope.snapshot(), "input": input})
                try:
                    parsed = module.config.model_validate(rendered)
                except ValueError as error:  # pydantic.ValidationError is a ValueError
                    return fail(f'Invalid config for "{node.use}": {_validation_summary(error)}')
                base: dict[str, Any] = {
                    "run_id": run_id,
                    "item_id": item_id,
                    "node_id": node.id,
                    "attempt": attempt,
                    "cwd": cwd_cell[0],
                    "scope": scope,
                    "registry": registry,
                    "bus": bus,
                    "log": log,
                    "signal": signal,
                    "record_cost": record_cost,
                    "step_key": f"{run_id}:{item_id}:{node.id}",
                    "store": store,
                    "now": now,
                    "watch": watching,
                    "lineage": lineage,
                    "locks": locks,
                    "agent_slots": agent_slots,
                }
                if resolved.kind == "action":
                    cache_key = (
                        _cache_key(module, parsed, input)
                        if config.cache and replay is None
                        else None
                    )
                    hit = store.cache_get(cache_key) if cache_key else None
                    replaying = (
                        replay is not None
                        and module.effects != "pure"
                        and not scope.get("no_replay")
                    )
                    recorded = (
                        replay.get((item_id, node.id, attempt))
                        if replay is not None and replaying
                        else None
                    )
                    if replaying and recorded is None and not resume:
                        return fail(f"Replay diverged: no recorded result for {node.id}#{attempt}")
                    if recorded is not None:
                        emit = Emit(recorded[0], recorded[1])
                        bus.emit(
                            "step.replayed",
                            run_id,
                            {"node": node.id},
                            item_id=item_id,
                            step_id=step_id,
                        )
                    elif hit is not None:
                        emit = Emit(hit["port"], hit["value"])
                        bus.emit(
                            "step.cached",
                            run_id,
                            {"node": node.id},
                            item_id=item_id,
                            step_id=step_id,
                        )
                    else:
                        step_context = create_step_context(parsed, **base)
                        emit = await _run_action(module, input, step_context, bus, step_id)
                        if cache_key and emit.port == "ok":
                            store.cache_put(
                                cache_key, {"port": emit.port, "value": emit.value}, now()
                            )
                    out_port = next((p for p in module.ports.outputs if p.name == "out"), None)
                    if emit.port == "ok" and emit.value is not None and out_port is not None:
                        check = registry.caps.validate(out_port.cap, emit.value)
                        if not check.ok:
                            return fail(
                                f'Output of "{node.use}" does not match cap {out_port.cap}: '
                                + "; ".join(check.errors)
                            )
                else:
                    step_ctx = create_step_context(parsed, **base)

                    async def run_child(
                        child: PipelineNode,
                        child_input: PortValues,
                        child_scope: Scope | None = None,
                        /,
                    ) -> Emit:
                        return await execute(child, child_input, child_scope or scope)

                    ctx = CompositeContext(
                        config=step_ctx.config,
                        run_id=step_ctx.run_id,
                        item_id=step_ctx.item_id,
                        node_id=step_ctx.node_id,
                        attempt=step_ctx.attempt,
                        cwd=step_ctx.cwd,
                        scope=step_ctx.scope,
                        registry=step_ctx.registry,
                        bus=step_ctx.bus,
                        log=step_ctx.log,
                        signal=step_ctx.signal,
                        record_cost=step_ctx.record_cost,
                        step_key=step_ctx.step_key,
                        store=store,
                        now=now,
                        watch=watching,
                        lineage=lineage,
                        locks=locks,
                        agent_slots=agent_slots,
                        policy=policy,
                        run=run_child,
                    )
                    emit = await module.run(node.children, input, ctx)
                return finish(emit)
            except Exception as error:
                return fail(_error_message(error))
            finally:
                try:
                    await scope.dispose()
                except Exception as error:
                    log("warn", f"Failed to release resources for {node.id}: {error}", None)

        set_state("running")
        try:
            provider = resolve_workspace(registry, config.extra)
            spec = workspace_spec(config.extra)
            if provider is not None and spec is not None:
                workspace = await provider.create(
                    render_deep(spec[1], item_scope.snapshot()), item_scope.get("item"), cwd
                )
                cwd_cell[0] = workspace.path
                bus.emit(
                    "workspace.created",
                    run_id,
                    {"kind": spec[0], "path": workspace.path},
                    item_id=item.id,
                )
            emit = await execute(config.root, {}, item_scope)
        except Exception as error:
            emit = Emit("fail", {"error": f"workspace: {error}"})
        await item_scope.dispose()
        if signal.aborted or cancelled_in_store():
            state: ItemState = "cancelled"
        elif is_outcome_port(emit.port):
            state = item_state_for(emit.port, config.on_fail)
        else:
            state = "done"
        set_state(state)
        if workspace is not None:
            try:
                await workspace.release(state)
                bus.emit("workspace.released", run_id, {"state": state}, item_id=item.id)
            except Exception as error:
                log("warn", f"Failed to release the workspace of {item.id}: {error}", None)
        finished[item.id] = state
        results[item.id] = ItemOutcome(item.id, state, emit.port, emit.value)

    done_events = {i.id: asyncio.Event() for i in items}

    async def worker(item: RunItem) -> None:
        try:
            if item.id in cycle_ids:
                block(item, ["dependency cycle"])
                return
            for dep in item.depends_on:
                if dep in done_events:
                    await done_events[dep].wait()
            blocked_by = [d for d in item.depends_on if finished.get(d) != "done"]
            if blocked_by:
                block(item, blocked_by)
                return
            async with slots:
                await run_item(item)
        finally:
            done_events[item.id].set()

    await asyncio.gather(*(worker(i) for i in items))
    outcomes = [results[i.id] for i in items]
    states = {o.state for o in outcomes}
    status: RunStatus = (
        "cancelled"
        if "cancelled" in states
        else "failed"
        if "failed" in states
        else "parked"
        if states & {"parked", "pending"}
        else "done"
    )
    store.finish_run(run_id, status, now())
    bus.emit("run.finished", run_id, {"status": status})
    unsubscribe()
    return RunResult(run_id=run_id, status=status, items=outcomes)


async def _run_action(
    module: Any, input: PortValues, ctx: StepContext[Any], bus: EventBus, step_id: int
) -> Emit:
    """Runs an action; journaled effects are looked up first and recorded after they succeed."""
    if not (module.journal and module.idempotency_key is not None) or ctx.store is None:
        return await module.run(input, ctx)
    store, run_id, now = ctx.store, ctx.run_id, ctx.now
    key = module.idempotency_key(input, ctx)
    known = store.effect_get(key)
    if known is not None and known.status == "done":
        bus.emit(
            "step.journaled",
            run_id,
            {"node": ctx.node_id, "key": key},
            item_id=ctx.item_id,
            step_id=step_id,
        )
        recorded: dict[str, Any] = known.ref
        return Emit(recorded["port"], recorded["value"])
    store.effect_put(key, run_id=run_id, status="started", ref=None, created_at=now())
    emit = await module.run(input, ctx)
    if emit.port == "ok":
        store.effect_put(
            key,
            run_id=run_id,
            status="done",
            ref={"port": emit.port, "value": emit.value},
            created_at=now(),
        )
    return emit


def find_dependency_cycles(items: list[RunItem]) -> list[list[str]]:
    """Groups of items that wait on each other (including an item waiting on itself)."""
    graph = {i.id: [d for d in i.depends_on if d in {x.id for x in items}] for i in items}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph[node]:
            if dep not in index:
                visit(dep)
                low[node] = min(low[node], low[dep])
            elif dep in on_stack:
                low[node] = min(low[node], index[dep])
        if low[node] == index[node]:
            group: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                group.append(member)
                if member == node:
                    break
            if len(group) > 1 or node in graph[node]:
                cycles.append(sorted(group))

    for item in items:
        if item.id not in index:
            visit(item.id)
    return sorted(cycles)


def _validation_summary(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(str(e["msg"]) for e in error.errors())
    return str(error)
