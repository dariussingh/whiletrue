"""Running one item: its scope, workspace, steps, and final state."""

from __future__ import annotations

from typing import Any, cast

from whiletrue.core.context import Scope
from whiletrue.core.module import Emit
from whiletrue.core.outcomes import ItemState, is_outcome_port, item_state_for, transition
from whiletrue.core.runtime.env import RunEnv
from whiletrue.core.runtime.model import ItemOutcome, RunItem
from whiletrue.core.runtime.steps import StepExecutor
from whiletrue.core.template import render_deep
from whiletrue.core.workspace import Workspace, resolve_workspace, workspace_spec


def item_scope(env: RunEnv, item: RunItem) -> Scope:
    """The values every step of an item sees: the run, the project, sessions and the item."""
    scope = Scope()
    scope.set("run", {"id": env.run_id})
    #: The project directory (steps may run in a workspace under it).
    scope.set("project_dir", env.cwd)
    #: Agent session ids shared across this item's steps (`session: item` or a name).
    scope.set("sessions", {})
    payload: Any = item.payload
    fields: dict[str, Any] = {}
    if isinstance(payload, dict):
        fields = {str(k): v for k, v in cast("dict[Any, Any]", payload).items()}
    scope.set("item", {"id": item.id, **fields})
    return scope


class ItemRunner:
    """Takes one item from `ready` to a final state."""

    def __init__(self, env: RunEnv, item: RunItem) -> None:
        self._env = env
        self._item = item
        self._state: ItemState = "ready"
        self._steps = StepExecutor(env, item.id)

    def _set_state(self, state: ItemState) -> None:
        env = self._env
        self._state = transition(self._state, state)
        env.store.set_item_state(env.run_id, self._item.id, state)
        env.bus.emit("item.state", env.run_id, {"state": state}, item_id=self._item.id)

    def _final_state(self, emit: Emit) -> ItemState:
        if self._env.signal.aborted or self._steps.cancelled():
            return "cancelled"
        if is_outcome_port(emit.port):
            return item_state_for(emit.port, self._env.config.on_fail)
        return "done"

    async def _create_workspace(self, scope: Scope) -> Workspace | None:
        env = self._env
        provider = resolve_workspace(env.registry, env.config.extra)
        spec = workspace_spec(env.config.extra)
        if provider is None or spec is None:
            return None
        workspace = await provider.create(
            render_deep(spec[1], scope.snapshot()), scope.get("item"), env.cwd
        )
        self._steps.cwd = workspace.path
        env.bus.emit(
            "workspace.created",
            env.run_id,
            {"kind": spec[0], "path": workspace.path},
            item_id=self._item.id,
        )
        return workspace

    async def _release_workspace(self, workspace: Workspace, state: ItemState) -> None:
        env = self._env
        try:
            await workspace.release(state)
            env.bus.emit("workspace.released", env.run_id, {"state": state}, item_id=self._item.id)
        except Exception as error:
            env.log("warn", f"Failed to release the workspace of {self._item.id}: {error}", None)

    async def run(self) -> ItemOutcome:
        env, item = self._env, self._item
        scope = item_scope(env, item)
        self._set_state("running")
        workspace: Workspace | None = None
        try:
            workspace = await self._create_workspace(scope)
            emit = await self._steps.execute(env.config.root, {}, scope)
        except Exception as error:
            emit = Emit("fail", {"error": f"workspace: {error}"})
        await scope.dispose()
        state = self._final_state(emit)
        self._set_state(state)
        if workspace is not None:
            await self._release_workspace(workspace, state)
        return ItemOutcome(item.id, state, emit.port, emit.value)
