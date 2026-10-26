"""Running a pipeline.

model      RunItem, ItemOutcome, RunResult, the pipeline hash, the dependency graph
scheduler  run_pipeline: items wait for dependencies and slots
items      ItemRunner: one item's scope, workspace, steps and final state
steps      StepExecutor: one node (record, resolve config, run, record the result)
journal    result cache and the side-effect journal
env        what a run shares
"""

from whiletrue.core.runtime.model import (
    ItemOutcome,
    RunItem,
    RunResult,
    find_dependency_cycles,
    hash_pipeline,
)
from whiletrue.core.runtime.scheduler import run_pipeline

__all__ = [
    "ItemOutcome",
    "RunItem",
    "RunResult",
    "find_dependency_cycles",
    "hash_pipeline",
    "run_pipeline",
]
