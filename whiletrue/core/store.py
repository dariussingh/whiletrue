from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from whiletrue.core.events import WtEvent
from whiletrue.core.history import (
    CommentRecord,
    Decision,
    EffectRecord,
    LearningRecord,
    LearningStatus,
    ParkRecord,
    SteerRecord,
)
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
    def interrupt_run(self, id: str, ended_at: int) -> None:
        """A run whose process died: its running steps errored, running items parked, run parked."""
        ...

    def latest_run(self) -> RunRecord | None: ...

    def upsert_item(self, item: ItemRecord) -> None: ...
    def set_item_state(self, run_id: str, item_id: str, state: ItemState) -> None: ...
    def list_items(self, run_id: str) -> list[ItemRecord]: ...
    def get_item(self, run_id: str, item_id: str) -> ItemRecord | None: ...

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

    # ---- human input and side effects (keyed by item, not run) ----

    def create_park(
        self,
        *,
        run_id: str,
        item_id: str,
        node_path: str,
        what: str,
        wake: dict[str, Any],
        reason: str,
        created_at: int,
    ) -> ParkRecord: ...
    def decide_park(
        self,
        park_id: int,
        decision: Decision,
        *,
        decided_by: str,
        decided_at: int,
        notes: str | None,
    ) -> None: ...
    def list_parks(
        self,
        item_id: str | None = None,
        *,
        node_path: str | None = None,
        run_id: str | None = None,
        undecided_only: bool = False,
    ) -> list[ParkRecord]:
        """Parks in creation order. The Nth execution of a gate uses the Nth of its parks."""
        ...

    def add_comment(
        self,
        *,
        item_id: str,
        source: str,
        external_id: str,
        author: str,
        body: str,
        created_at: int,
        thread: str | None = None,
        url: str | None = None,
        kind: Literal["human", "reviewer"] = "human",
    ) -> CommentRecord | None:
        """Stores a comment; None when (item, source, external id) was already recorded."""
        ...

    def list_comments(
        self, item_id: str, *, unanswered_only: bool = False
    ) -> list[CommentRecord]: ...
    def answer_comment(self, comment_id: int, reply: str) -> None: ...

    def add_steer(
        self, *, run_id: str, item_id: str, step: str, prompt: str, created_at: int
    ) -> SteerRecord: ...
    def list_steers(self, item_id: str) -> list[SteerRecord]: ...

    def effect_get(self, key: str) -> EffectRecord | None: ...
    def effect_put(
        self,
        key: str,
        *,
        run_id: str,
        status: Literal["started", "done"],
        ref: Any,
        created_at: int,
    ) -> None: ...

    def add_learning(
        self,
        *,
        scope: str,
        note: str,
        status: LearningStatus,
        created_at: int,
        run_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> LearningRecord: ...
    def list_learnings(
        self, *, scope: str | None = None, status: LearningStatus | None = None
    ) -> list[LearningRecord]: ...
    def set_learning_status(self, learning_id: int, status: LearningStatus) -> None: ...

    def close(self) -> None: ...
