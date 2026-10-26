"""What every step and item of one run shares."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from whiletrue.abort import AbortSignal
from whiletrue.core.config import PipelineConfig
from whiletrue.core.context import Locks, Logger
from whiletrue.core.events import EventBus
from whiletrue.core.outcomes import OutcomePolicy
from whiletrue.core.registry import Registry
from whiletrue.core.store import StateStore


@dataclass
class RunEnv:
    """The run-wide services and settings. Built once by `run_pipeline`."""

    registry: Registry
    config: PipelineConfig
    store: StateStore
    bus: EventBus
    signal: AbortSignal
    log: Logger
    now: Callable[[], int]
    policy: OutcomePolicy
    run_id: str
    #: The project directory.
    cwd: str
    #: Gates wait for decisions instead of parking the item.
    watching: bool
    #: The first run of a chain of resumes; a gate's parks belong to it.
    lineage: str
    locks: Locks
    #: Caps live agent sessions across the run.
    agent_slots: Any
    replay: dict[tuple[str, str, int], tuple[str, Any]] | None = None
    resume: bool = False
    #: How many times each (item, node) has run, so replays find their recording.
    attempts: dict[str, int] = field(default_factory=lambda: {})

    def next_attempt(self, item_id: str, node_id: str) -> int:
        key = f"{item_id}:{node_id}"
        self.attempts[key] = self.attempts.get(key, 0) + 1
        return self.attempts[key]
