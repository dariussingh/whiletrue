from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from whiletrue.abort import AbortSignal
from whiletrue.core.events import EventBus
from whiletrue.core.registry import Registry

if TYPE_CHECKING:
    from whiletrue.core.config import PipelineNode
    from whiletrue.core.module import Emit, PortValues
    from whiletrue.core.outcomes import OutcomePolicy
    from whiletrue.core.store import StateStore


class Locks:
    """Named locks shared by a run's steps (`repo:main` so only one item merges at a time)."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def hold(self, name: str) -> asyncio.Lock:
        """Use as `async with locks.hold("repo:main"):`."""
        return self._locks.setdefault(name, asyncio.Lock())


@dataclass
class Resource:
    name: str
    dispose: Callable[[], Awaitable[None] | None]


class Scope:
    """Key-value context inherited by children, plus owned resources (workspaces, sessions, panes).

    Resources are released when the scope exits, which makes "one worktree per issue" a matter of
    where you declare it.
    """

    def __init__(self, parent: Scope | None = None) -> None:
        self.parent = parent
        self._values: dict[str, Any] = {}
        self._resources: list[Resource] = []
        self._disposed = False

    def get(self, key: str) -> Any:
        if key in self._values:
            return self._values[key]
        return self.parent.get(key) if self.parent is not None else None

    def has(self, key: str) -> bool:
        return key in self._values or (self.parent.has(key) if self.parent is not None else False)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def snapshot(self) -> dict[str, Any]:
        """All visible values; children shadow parents. Used as the template scope."""
        inherited = self.parent.snapshot() if self.parent is not None else {}
        return {**inherited, **self._values}

    def child(self) -> Scope:
        return Scope(self)

    def add_resource(self, resource: Resource) -> None:
        if self._disposed:
            raise RuntimeError(f'Scope already disposed; cannot add "{resource.name}"')
        self._resources.append(resource)

    @property
    def resource_names(self) -> list[str]:
        return [r.name for r in self._resources]

    async def dispose(self) -> None:
        """Releases resources in reverse order. Every disposer runs; the first failure is raised."""
        if self._disposed:
            return
        self._disposed = True
        first: Exception | None = None
        for resource in reversed(self._resources):
            try:
                result = resource.dispose()
                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                if first is None:
                    first = error
        if first is not None:
            raise first


class Logger(Protocol):
    def __call__(self, level: str, message: str, data: dict[str, Any] | None = None) -> None: ...


def _noop_log(level: str, message: str, data: dict[str, Any] | None = None) -> None:
    return None


def _noop_cost(usd: float) -> None:
    return None


@dataclass(kw_only=True)
class StepContext[Cfg]:
    """Everything an action can see."""

    config: Cfg
    run_id: str
    item_id: str
    #: The step's id in the pipeline tree.
    node_id: str
    attempt: int
    #: Working directory for this step (project root or a workspace).
    cwd: str
    scope: Scope
    registry: Registry
    bus: EventBus
    log: Logger
    signal: AbortSignal
    #: Adds spend (USD) to this step's recorded cost.
    record_cost: Callable[[float], None]
    #: Stable per-execution key, e.g. for idempotency: `<run>:<item>:<node>`.
    step_key: str
    #: The run's state store (human gates, comments and the effect journal live here).
    store: StateStore | None = None
    #: Epoch milliseconds; injectable so tests control time.
    now: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    #: `on_idle: watch`: gates wait for a decision instead of parking the item.
    watch: bool = False
    #: Parks created by this chain of resumed runs belong to this id.
    lineage: str = ""
    locks: Locks = field(default_factory=Locks)


@dataclass(kw_only=True)
class CompositeContext[Cfg](StepContext[Cfg]):
    """Everything a strategy can see. Children run only through `run`, so each is recorded."""

    policy: OutcomePolicy
    run: Callable[[PipelineNode, PortValues], Awaitable[Emit]] = field(init=True)


def create_step_context[Cfg](config: Cfg, **overrides: Any) -> StepContext[Cfg]:
    """Builds a context with sensible defaults; used by the scheduler and by tests."""
    run_id: str = overrides.pop("run_id", "run")
    item_id: str = overrides.pop("item_id", "item")
    node_id: str = overrides.pop("node_id", "node")
    values: dict[str, Any] = {
        "attempt": 1,
        "cwd": os.getcwd(),
        "scope": Scope(),
        "registry": Registry(),
        "bus": EventBus(),
        "log": _noop_log,
        "record_cost": _noop_cost,
        "signal": AbortSignal(),
        "step_key": f"{run_id}:{item_id}:{node_id}",
    }
    values.update(overrides)
    return StepContext(config=config, run_id=run_id, item_id=item_id, node_id=node_id, **values)
