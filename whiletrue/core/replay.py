"""Replay: re-run a recorded run's logic without running any module that has effects.

Every step's port and output is already recorded. A replay serves those recordings for all
modules except `pure` ones, so control flow, expressions and wiring run for real while agents,
shells and gates are not executed again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from whiletrue.core.runtime import RunItem
from whiletrue.core.store import StateStore

#: (item id, node path, attempt) -> recorded (port, output)
ReplayData = dict[tuple[str, str, int], tuple[str, Any]]


@dataclass
class Recording:
    run_id: str
    status: str
    pipeline_hash: str
    items: list[RunItem]
    #: Recorded terminal state per item id.
    item_states: dict[str, str]
    steps: ReplayData
    #: The chain of resumes this run belongs to (its own id for a fresh run).
    lineage: str = ""


class ReplayError(Exception):
    pass


def load_recording(store: StateStore, run_id: str) -> Recording:
    run = store.get_run(run_id)
    if run is None:
        raise ReplayError(f'No run "{run_id}"')
    records = store.list_items(run_id)
    steps: ReplayData = {
        (s.item_id, s.node_path, s.attempt): (s.port, s.output)
        for s in store.list_steps(run_id)
        # A step that parked for a person must run again when its run is resumed.
        if s.port is not None and s.port != "needs_human"
    }
    started = store.list_events(run_id, type="run.started")
    lineage = str(started[0].data.get("lineage", run_id)) if started else run_id
    return Recording(
        run_id=run.id,
        status=run.status,
        pipeline_hash=run.pipeline_hash,
        items=[RunItem(i.id, i.payload) for i in records],
        item_states={i.id: i.state for i in records},
        steps=steps,
        lineage=lineage,
    )
