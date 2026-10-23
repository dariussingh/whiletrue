"""Records kept across runs: human input (parks, comments, steers), side effects and learnings.

Unlike runs and steps, these are keyed by *item*, not run: a gate parked in one process is decided
from another, and a comment made today is answered by tomorrow's run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Decision = Literal["approved", "rejected"]
LearningStatus = Literal["proposed", "approved", "rejected"]


@dataclass
class ParkRecord:
    id: int
    #: The run lineage (the first run of a chain of resumes) that created it.
    run_id: str
    item_id: str
    #: The gate step that parked the item.
    node_path: str
    #: What is being decided (`pr`, `merge`, ...), shown in status.
    what: str
    #: The wake condition this park waits on.
    wake: dict[str, Any]
    reason: str
    created_at: int
    decision: Decision | None = None
    notes: str | None = None
    decided_by: str | None = None
    decided_at: int | None = None


@dataclass
class CommentRecord:
    id: int
    item_id: str
    #: Where it came from: `github_pr`, `local`, `ai_review`, ...
    source: str
    #: The id in that system, unique per (item, source).
    external_id: str
    author: str
    body: str
    created_at: int
    thread: str | None = None
    url: str | None = None
    #: `human` comments get answered but never resolved by the agent; `reviewer` is automated.
    kind: Literal["human", "reviewer"] = "human"
    answered: bool = False
    reply: str | None = None


@dataclass
class SteerRecord:
    id: int
    run_id: str
    item_id: str
    step: str
    prompt: str
    created_at: int


@dataclass
class EffectRecord:
    #: A stable key naming the effect (`github.open_pr:owner/repo:wt/12`), not the run.
    key: str
    status: Literal["started", "done"]
    run_id: str
    created_at: int
    ref: Any = None


@dataclass
class LearningRecord:
    id: int
    scope: str
    note: str
    status: LearningStatus
    created_at: int
    run_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=lambda: {})
