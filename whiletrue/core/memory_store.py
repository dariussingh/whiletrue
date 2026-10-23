from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Literal

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
from whiletrue.core.store import ItemRecord, RunRecord, StepRecord


class MemoryStore:
    """In-memory StateStore for tests and throwaway runs."""

    def __init__(self) -> None:
        self._runs: list[RunRecord] = []
        self._items: list[ItemRecord] = []
        self._steps: list[StepRecord] = []
        self._events: list[WtEvent] = []
        self._cache: dict[str, Any] = {}
        self._parks: list[ParkRecord] = []
        self._comments: list[CommentRecord] = []
        self._steers: list[SteerRecord] = []
        self._effects: dict[str, EffectRecord] = {}
        self._learnings: list[LearningRecord] = []

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

    def interrupt_run(self, id: str, ended_at: int) -> None:
        for step in self._steps:
            if step.run_id == id and step.status == "running":
                step.status, step.error, step.ended_at = "errored", "interrupted", ended_at
        for item in self._items:
            if item.run_id == id and item.state == "running":
                item.state = "parked"
        self.finish_run(id, "parked", ended_at)

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

    def get_item(self, run_id: str, item_id: str) -> ItemRecord | None:
        found = next((i for i in self._items if i.run_id == run_id and i.id == item_id), None)
        return replace(found) if found else None

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

    # ---- human input and side effects ----

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
    ) -> ParkRecord:
        park = ParkRecord(
            id=len(self._parks) + 1,
            run_id=run_id,
            item_id=item_id,
            node_path=node_path,
            what=what,
            wake=copy.deepcopy(wake),
            reason=reason,
            created_at=created_at,
        )
        self._parks.append(park)
        return replace(park)

    def _park(self, park_id: int) -> ParkRecord:
        park = next((p for p in self._parks if p.id == park_id), None)
        if park is None:
            raise KeyError(f"No park {park_id}")
        return park

    def decide_park(
        self,
        park_id: int,
        decision: Decision,
        *,
        decided_by: str,
        decided_at: int,
        notes: str | None,
    ) -> None:
        park = self._park(park_id)
        park.decision, park.decided_by, park.decided_at, park.notes = (
            decision,
            decided_by,
            decided_at,
            notes,
        )

    def list_parks(
        self,
        item_id: str | None = None,
        *,
        node_path: str | None = None,
        run_id: str | None = None,
        undecided_only: bool = False,
    ) -> list[ParkRecord]:
        return [
            replace(p)
            for p in self._parks
            if (item_id is None or p.item_id == item_id)
            and (node_path is None or p.node_path == node_path)
            and (run_id is None or p.run_id == run_id)
            and (not undecided_only or p.decision is None)
        ]

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
        if any(
            c.item_id == item_id and c.source == source and c.external_id == external_id
            for c in self._comments
        ):
            return None
        comment = CommentRecord(
            id=len(self._comments) + 1,
            item_id=item_id,
            source=source,
            external_id=external_id,
            author=author,
            body=body,
            created_at=created_at,
            thread=thread,
            url=url,
            kind=kind,
        )
        self._comments.append(comment)
        return replace(comment)

    def list_comments(self, item_id: str, *, unanswered_only: bool = False) -> list[CommentRecord]:
        return [
            replace(c)
            for c in self._comments
            if c.item_id == item_id and not (unanswered_only and c.answered)
        ]

    def answer_comment(self, comment_id: int, reply: str) -> None:
        comment = next((c for c in self._comments if c.id == comment_id), None)
        if comment is None:
            raise KeyError(f"No comment {comment_id}")
        comment.answered, comment.reply = True, reply

    def add_steer(
        self, *, run_id: str, item_id: str, step: str, prompt: str, created_at: int
    ) -> SteerRecord:
        steer = SteerRecord(len(self._steers) + 1, run_id, item_id, step, prompt, created_at)
        self._steers.append(steer)
        return replace(steer)

    def list_steers(self, item_id: str) -> list[SteerRecord]:
        return [replace(s) for s in self._steers if s.item_id == item_id]

    def effect_get(self, key: str) -> EffectRecord | None:
        found = self._effects.get(key)
        return None if found is None else replace(found, ref=copy.deepcopy(found.ref))

    def effect_put(
        self,
        key: str,
        *,
        run_id: str,
        status: Literal["started", "done"],
        ref: Any,
        created_at: int,
    ) -> None:
        self._effects[key] = EffectRecord(key, status, run_id, created_at, copy.deepcopy(ref))

    def add_learning(
        self,
        *,
        scope: str,
        note: str,
        status: LearningStatus,
        created_at: int,
        run_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> LearningRecord:
        learning = LearningRecord(
            len(self._learnings) + 1,
            scope,
            note,
            status,
            created_at,
            run_id,
            copy.deepcopy(provenance or {}),
        )
        self._learnings.append(learning)
        return replace(learning)

    def list_learnings(
        self, *, scope: str | None = None, status: LearningStatus | None = None
    ) -> list[LearningRecord]:
        return [
            replace(item)
            for item in self._learnings
            if (scope is None or item.scope == scope) and (status is None or item.status == status)
        ]

    def set_learning_status(self, learning_id: int, status: LearningStatus) -> None:
        item = next((x for x in self._learnings if x.id == learning_id), None)
        if item is None:
            raise KeyError(f"No learning {learning_id}")
        item.status = status
