from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from whiletrue.core.events import WtEvent
from whiletrue.core.outcomes import ItemState

RunStatus = Literal["running", "done", "failed", "parked", "cancelled"]
StepStatus = Literal["running", "completed", "errored"]


@dataclass
class RunRecord:
    id: str
    pipeline_hash: str
    lockfile: str | None
    status: RunStatus
    started_at: int
    ended_at: int | None


@dataclass
class ItemRecord:
    id: str
    run_id: str
    source_key: str
    state: ItemState
    payload: Any = None
    depends_on: list[str] = field(default_factory=lambda: [])


@dataclass
class StepRecord:
    id: int
    run_id: str
    item_id: str
    node_path: str
    attempt: int
    status: StepStatus
    #: Port the step fired (ok, fail, ...), once completed.
    port: str | None
    input: Any
    output: Any
    cost_usd: float
    error: str | None
    started_at: int
    ended_at: int | None


class StateStore(Protocol):
    """Runtime source of truth for pipeline progress.

    SQLite is the only v1 backend; everything else (GitHub, git, ...) is an optional integration
    elsewhere.
    """

    def create_run(
        self, *, id: str, pipeline_hash: str, started_at: int, lockfile: str | None = None
    ) -> None: ...
    def finish_run(
        self, id: str, status: Literal["done", "failed", "parked", "cancelled"], ended_at: int
    ) -> None: ...
    def get_run(self, id: str) -> RunRecord | None: ...
    def latest_run(self) -> RunRecord | None: ...

    def upsert_item(self, item: ItemRecord) -> None: ...
    def set_item_state(self, run_id: str, item_id: str, state: ItemState) -> None: ...
    def list_items(self, run_id: str) -> list[ItemRecord]: ...

    def start_step(
        self,
        *,
        run_id: str,
        item_id: str,
        node_path: str,
        attempt: int,
        input: Any,
        started_at: int,
    ) -> int: ...
    def finish_step(
        self,
        step_id: int,
        *,
        status: Literal["completed", "errored"],
        ended_at: int,
        port: str | None = None,
        output: Any = None,
        cost_usd: float = 0,
        error: str | None = None,
    ) -> None: ...
    def list_steps(self, run_id: str, item_id: str | None = None) -> list[StepRecord]: ...

    def append_event(self, event: WtEvent) -> None: ...
    def list_events(
        self, run_id: str, *, item_id: str | None = None, type: str | None = None
    ) -> list[WtEvent]: ...

    def cache_get(self, key: str) -> Any | None:
        """The value stored under `key`, or None on a miss (callers wrap values so None is free)."""
        ...

    def cache_put(self, key: str, value: Any, created_at: int) -> None: ...

    def close(self) -> None: ...
