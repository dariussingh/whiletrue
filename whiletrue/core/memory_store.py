from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Literal

from whiletrue.core.events import WtEvent
from whiletrue.core.outcomes import ItemState
from whiletrue.core.store import ItemRecord, RunRecord, StepRecord


class MemoryStore:
    """In-memory StateStore for tests and throwaway runs."""

    def __init__(self) -> None:
        self._runs: list[RunRecord] = []
        self._items: list[ItemRecord] = []
        self._steps: list[StepRecord] = []
        self._events: list[WtEvent] = []
        self._cache: dict[str, Any] = {}

    def create_run(
        self, *, id: str, pipeline_hash: str, started_at: int, lockfile: str | None = None
    ) -> None:
        self._runs.append(
            RunRecord(
                id=id,
                pipeline_hash=pipeline_hash,
                lockfile=lockfile,
                status="running",
                started_at=started_at,
                ended_at=None,
            )
        )

    def finish_run(
        self, id: str, status: Literal["done", "failed", "parked", "cancelled"], ended_at: int
    ) -> None:
        run = self.get_run(id)
        if run is None:
            raise KeyError(f'No run "{id}"')
        run.status = status
        run.ended_at = ended_at

    def get_run(self, id: str) -> RunRecord | None:
        return next((r for r in self._runs if r.id == id), None)

    def latest_run(self) -> RunRecord | None:
        return self._runs[-1] if self._runs else None

    def upsert_item(self, item: ItemRecord) -> None:
        stored = replace(item, depends_on=list(item.depends_on))
        for i, existing in enumerate(self._items):
            if existing.run_id == item.run_id and existing.id == item.id:
                self._items[i] = stored
                return
        self._items.append(stored)

    def set_item_state(self, run_id: str, item_id: str, state: ItemState) -> None:
        for item in self._items:
            if item.run_id == run_id and item.id == item_id:
                item.state = state
                return
        raise KeyError(f'No item "{item_id}" in run "{run_id}"')

    def list_items(self, run_id: str) -> list[ItemRecord]:
        return [replace(i) for i in self._items if i.run_id == run_id]

    def start_step(
        self,
        *,
        run_id: str,
        item_id: str,
        node_path: str,
        attempt: int,
        input: Any,
        started_at: int,
    ) -> int:
        step_id = len(self._steps) + 1
        self._steps.append(
            StepRecord(
                id=step_id,
                run_id=run_id,
                item_id=item_id,
                node_path=node_path,
                attempt=attempt,
                status="running",
                port=None,
                input=input,
                output=None,
                cost_usd=0,
                error=None,
                started_at=started_at,
                ended_at=None,
            )
        )
        return step_id

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
    ) -> None:
        step = next((s for s in self._steps if s.id == step_id), None)
        if step is None:
            raise KeyError(f"No step {step_id}")
        step.status = status
        step.port = port
        step.output = output
        step.cost_usd = cost_usd
        step.error = error
        step.ended_at = ended_at

    def list_steps(self, run_id: str, item_id: str | None = None) -> list[StepRecord]:
        return [
            replace(s)
            for s in self._steps
            if s.run_id == run_id and (item_id is None or s.item_id == item_id)
        ]

    def append_event(self, event: WtEvent) -> None:
        self._events.append(copy.copy(event))

    def list_events(
        self, run_id: str, *, item_id: str | None = None, type: str | None = None
    ) -> list[WtEvent]:
        return [
            e
            for e in self._events
            if e.run_id == run_id
            and (item_id is None or e.item_id == item_id)
            and (type is None or e.type == type)
        ]

    def cache_get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def cache_put(self, key: str, value: Any, created_at: int) -> None:
        self._cache[key] = value

    def close(self) -> None:
        return None
