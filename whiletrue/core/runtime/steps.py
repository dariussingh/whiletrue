"""Running one pipeline node: record it, resolve its config, run it, record the result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from whiletrue.core.config import PipelineNode
from whiletrue.core.context import CompositeContext, Scope, create_step_context
from whiletrue.core.module import Emit, PortValues
from whiletrue.core.runtime.env import RunEnv
from whiletrue.core.runtime.journal import cache_key, run_journaled
from whiletrue.core.template import render_deep


class StepFailure(Exception):
    """A step failed before or after its module ran; the message is recorded as the error."""


class StepRecorder:
    """The store and event-log record of one step execution."""

    def __init__(
        self, env: RunEnv, item_id: str, node: PipelineNode, attempt: int, input: PortValues
    ) -> None:
        self._env = env
        self._item_id = item_id
        self._node = node
        self.cost = 0.0
        self.step_id = env.store.start_step(
            run_id=env.run_id,
            item_id=item_id,
            node_path=node.id,
            attempt=attempt,
            input=input,
            started_at=env.now(),
        )
        env.bus.emit(
            "step.started",
            env.run_id,
            {"node": node.id, "use": node.use, "attempt": attempt},
            item_id=item_id,
            step_id=self.step_id,
        )

    def record_cost(self, usd: float) -> None:
        self.cost += usd

    def event(self, kind: Any, data: dict[str, Any]) -> None:
        self._env.bus.emit(
            kind, self._env.run_id, data, item_id=self._item_id, step_id=self.step_id
        )

    def finish(self, emit: Emit, errored: str | None = None) -> Emit:
        env = self._env
        env.store.finish_step(
            self.step_id,
            status="errored" if errored else "completed",
            port=emit.port,
            output=emit.value,
            cost_usd=self.cost,
            error=errored,
            ended_at=env.now(),
        )
        data: dict[str, Any] = {"node": self._node.id, "port": emit.port, "cost_usd": self.cost}
        if errored:
            data["error"] = errored
        self.event("step.error" if errored else "step.finished", data)
        return emit

    def fail(self, message: str) -> Emit:
        return self.finish(Emit("fail", {"error": message}), message)


@dataclass
class StepCall:
    """Everything one step execution needs once its config has been resolved."""

    node: PipelineNode
    input: PortValues
    attempt: int
    scope: Scope
    fields: dict[str, Any]
    step: StepRecorder


def _validation_summary(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(str(e["msg"]) for e in error.errors())
    return str(error)


class StepExecutor:
    """Runs the nodes of one item. `cwd` is where its steps run (a workspace, once created)."""

    def __init__(self, env: RunEnv, item_id: str) -> None:
        self.env = env
        self.item_id = item_id
        self.cwd = env.cwd

    def cancelled(self) -> bool:
        record = self.env.store.get_item(self.env.run_id, self.item_id)
        return record is not None and record.state == "cancelled"

    async def execute(self, node: PipelineNode, input: PortValues, parent_scope: Scope) -> Emit:
        env = self.env
        resolved = env.registry.resolve(node.use)
        attempt = env.next_attempt(self.item_id, node.id)
        step = StepRecorder(env, self.item_id, node, attempt, input)
        if env.signal.aborted or self.cancelled():
            return step.fail("cancelled")
        if resolved is None:
            return step.fail(f'Unknown module "{node.use}"')
        scope = parent_scope.child()
        try:
            parsed = self._parse_config(resolved.module, node, input, scope)
            fields = self._context_fields(node, attempt, scope, step)
            call = StepCall(node, input, attempt, scope, fields, step)
            if resolved.kind == "action":
                emit = await self._run_action(resolved.module, parsed, call)
            else:
                emit = await self._run_composite(resolved.module, parsed, call)
            return step.finish(emit)
        except Exception as error:
            return step.fail(str(error))
        finally:
            try:
                await scope.dispose()
            except Exception as error:
                env.log("warn", f"Failed to release resources for {node.id}: {error}", None)

    def _parse_config(
        self, module: Any, node: PipelineNode, input: PortValues, scope: Scope
    ) -> Any:
        rendered = render_deep(node.config, {**scope.snapshot(), "input": input})
        try:
            return module.config.model_validate(rendered)
        except ValueError as error:  # pydantic.ValidationError is a ValueError
            raise StepFailure(
                f'Invalid config for "{node.use}": {_validation_summary(error)}'
            ) from error

    def _context_fields(
        self, node: PipelineNode, attempt: int, scope: Scope, step: StepRecorder
    ) -> dict[str, Any]:
        env = self.env
        return {
            "run_id": env.run_id,
            "item_id": self.item_id,
            "node_id": node.id,
            "attempt": attempt,
            #: A scope may redirect its steps to another directory (`rsi` rollouts).
            "cwd": str(scope.get("cwd") or self.cwd),
            "scope": scope,
            "registry": env.registry,
            "bus": env.bus,
            "log": env.log,
            "signal": env.signal,
            "record_cost": step.record_cost,
            "step_key": f"{env.run_id}:{self.item_id}:{node.id}",
            "store": env.store,
            "now": env.now,
            "watch": env.watching,
            "lineage": env.lineage,
            "locks": env.locks,
            "agent_slots": env.agent_slots,
        }

    async def _run_action(self, module: Any, parsed: Any, call: StepCall) -> Emit:
        """An action: a recorded replay, a cached result, or a real run (journaled if needed)."""
        env, node, input, step = self.env, call.node, call.input, call.step
        key = cache_key(module, parsed, input) if env.config.cache and env.replay is None else None
        hit = env.store.cache_get(key) if key else None
        replaying = (
            env.replay is not None and module.effects != "pure" and not call.scope.get("no_replay")
        )
        recorded = (
            env.replay.get((self.item_id, node.id, call.attempt))
            if env.replay is not None and replaying
            else None
        )
        if replaying and recorded is None and not env.resume:
            raise StepFailure(f"Replay diverged: no recorded result for {node.id}#{call.attempt}")
        if recorded is not None:
            emit = Emit(recorded[0], recorded[1])
            step.event("step.replayed", {"node": node.id})
        elif hit is not None:
            emit = Emit(hit["port"], hit["value"])
            step.event("step.cached", {"node": node.id})
        else:
            emit = await run_journaled(
                module, input, create_step_context(parsed, **call.fields), env.bus, step.step_id
            )
            if key and emit.port == "ok":
                env.store.cache_put(key, {"port": emit.port, "value": emit.value}, env.now())
        self._check_output(module, node, emit)
        return emit

    def _check_output(self, module: Any, node: PipelineNode, emit: Emit) -> None:
        out_port = next((p for p in module.ports.outputs if p.name == "out"), None)
        if emit.port == "ok" and emit.value is not None and out_port is not None:
            check = self.env.registry.caps.validate(out_port.cap, emit.value)
            if not check.ok:
                raise StepFailure(
                    f'Output of "{node.use}" does not match cap {out_port.cap}: '
                    + "; ".join(check.errors)
                )

    async def _run_composite(self, module: Any, parsed: Any, call: StepCall) -> Emit:
        """A strategy: it runs its children through `run`, so each is recorded."""

        async def run_child(
            child: PipelineNode, child_input: PortValues, child_scope: Scope | None = None, /
        ) -> Emit:
            return await self.execute(child, child_input, child_scope or call.scope)

        ctx = CompositeContext(config=parsed, **call.fields, policy=self.env.policy, run=run_child)
        return await module.run(call.node.children, call.input, ctx)
