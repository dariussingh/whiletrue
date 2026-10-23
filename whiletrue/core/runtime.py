from __future__ import annotations

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
from whiletrue.core.context import CompositeContext, Logger, Scope, create_step_context
from whiletrue.core.events import EventBus
from whiletrue.core.lock import collect_file_hashes
from whiletrue.core.module import Emit, PortValues
from whiletrue.core.outcomes import ItemState, OutcomePolicy, is_outcome_port, item_state_for
from whiletrue.core.registry import Registry
from whiletrue.core.store import ItemRecord, RunStatus, StateStore
from whiletrue.core.template import render_deep


@dataclass
class RunItem:
    id: str
    payload: Any = None


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
) -> RunResult:
    """Sequential scheduler: runs each item through the module tree and persists every step.

    (Parallel item-token scheduling replaces the item loop in a later milestone.)
    """
    bus = bus if bus is not None else EventBus()
    run_id = run_id if run_id is not None else str(uuid.uuid4())
    cwd = cwd if cwd is not None else os.getcwd()
    signal = signal if signal is not None else AbortSignal()
    log = log if log is not None else _noop_log
    items = items if items is not None else [RunItem("main")]
    policy = OutcomePolicy(on_fail=config.on_fail)

    pipeline_hash = hash_pipeline(config)
    store.create_run(id=run_id, pipeline_hash=pipeline_hash, started_at=now(), lockfile=lockfile)
    unsubscribe = bus.subscribe(store.append_event)
    bus.emit("run.started", run_id, {"pipeline": config.name, "items": len(items)})
    bus.emit(
        "run.hashes",
        run_id,
        {"pipeline": pipeline_hash, "files": collect_file_hashes(config, cwd)},
    )

    attempts: dict[str, int] = {}
    outcomes: list[ItemOutcome] = []

    for item in items:
        store.upsert_item(
            ItemRecord(
                id=item.id,
                run_id=run_id,
                source_key=item.id,
                state="ready",
                payload=item.payload,
                depends_on=[],
            )
        )

    for item in items:
        item_scope = Scope()
        item_scope.set("run", {"id": run_id})
        raw_payload: Any = item.payload
        fields: dict[str, Any] = {}
        if isinstance(raw_payload, dict):
            fields = {str(k): v for k, v in cast(dict[Any, Any], raw_payload).items()}
        item_scope.set("item", {"id": item.id, **fields})

        def set_state(state: ItemState, item_id: str = item.id) -> None:
            store.set_item_state(run_id, item_id, state)
            bus.emit("item.state", run_id, {"state": state}, item_id=item_id)

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

            if signal.aborted:
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
                    "cwd": cwd,
                    "scope": scope,
                    "registry": registry,
                    "bus": bus,
                    "log": log,
                    "signal": signal,
                    "record_cost": record_cost,
                    "step_key": f"{run_id}:{item_id}:{node.id}",
                }
                if resolved.kind == "action":
                    cache_key = _cache_key(module, parsed, input) if config.cache else None
                    hit = store.cache_get(cache_key) if cache_key else None
                    if hit is not None:
                        emit = Emit(hit["port"], hit["value"])
                        bus.emit(
                            "step.cached",
                            run_id,
                            {"node": node.id},
                            item_id=item_id,
                            step_id=step_id,
                        )
                    else:
                        emit = await module.run(input, create_step_context(parsed, **base))
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

                    async def run_child(child: PipelineNode, child_input: PortValues) -> Emit:
                        return await execute(child, child_input, scope)

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
        emit = await execute(config.root, {}, item_scope)
        await item_scope.dispose()
        if signal.aborted:
            state: ItemState = "cancelled"
        elif is_outcome_port(emit.port):
            state = item_state_for(emit.port, config.on_fail)
        else:
            state = "done"
        set_state(state)
        outcomes.append(ItemOutcome(item.id, state, emit.port, emit.value))

    states = {o.state for o in outcomes}
    status: RunStatus = (
        "cancelled"
        if "cancelled" in states
        else "failed"
        if "failed" in states
        else "parked"
        if "parked" in states
        else "done"
    )
    store.finish_run(run_id, status, now())
    bus.emit("run.finished", run_id, {"status": status})
    unsubscribe()
    return RunResult(run_id=run_id, status=status, items=outcomes)


def _validation_summary(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(str(e["msg"]) for e in error.errors())
    return str(error)
