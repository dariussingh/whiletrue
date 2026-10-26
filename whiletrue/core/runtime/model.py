"""What a run is made of and what it returns, plus the item dependency graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from whiletrue.core.config import PipelineConfig, PipelineNode
from whiletrue.core.outcomes import ItemState

FinalStatus = Literal["done", "failed", "parked", "cancelled"]


@dataclass
class RunItem:
    id: str
    payload: Any = None
    #: Ids of items that must be done first; the item waits (stays `pending`) until they are.
    depends_on: list[str] = field(default_factory=lambda: [])


@dataclass
class ItemOutcome:
    item_id: str
    state: ItemState
    port: str
    value: Any = None


@dataclass
class RunResult:
    run_id: str
    status: FinalStatus
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


def run_status(outcomes: list[ItemOutcome]) -> FinalStatus:
    """The status of a run from its items: cancelled beats failed beats parked beats done."""
    states = {o.state for o in outcomes}
    if "cancelled" in states:
        return "cancelled"
    if "failed" in states:
        return "failed"
    if states & {"parked", "pending"}:
        return "parked"
    return "done"


def find_dependency_cycles(items: list[RunItem]) -> list[list[str]]:
    """Groups of items that wait on each other (including an item waiting on itself)."""
    known = {x.id for x in items}
    graph = {i.id: [d for d in i.depends_on if d in known] for i in items}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for dep in graph[node]:
            if dep not in index:
                visit(dep)
                low[node] = min(low[node], low[dep])
            elif dep in on_stack:
                low[node] = min(low[node], index[dep])
        if low[node] == index[node]:
            group: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                group.append(member)
                if member == node:
                    break
            if len(group) > 1 or node in graph[node]:
                cycles.append(sorted(group))

    for item in items:
        if item.id not in index:
            visit(item.id)
    return sorted(cycles)
