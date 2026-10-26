"""A rollout: the wrapped steps run once, in an isolated copy of the item's workspace.

A rollout starts from a snapshot of the item's directory, optionally with a candidate harness
written into it, runs the strategy's child steps there (their working directory is redirected
through the scope), and records the trajectory and the files changed. A finished rollout is stored
on disk with its harness fingerprint, so a restart reuses it instead of running it again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from whiletrue.control.chain import run_chain
from whiletrue.core import Emit, PipelineNode
from whiletrue.core.module import PortValues
from whiletrue.rsi.harness2 import context, runmark
from whiletrue.rsi.harness2 import snapshot as sn
from whiletrue.rsi.harness2 import trajectory as tj
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.tree import Tree


@dataclass
class Rollout:
    label: str
    port: str
    value: Any
    final: str
    view: context.RolloutView
    changes: sn.Changes
    cost: float
    harness: Tree | None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.port == "ok"

    @property
    def delivered(self) -> bool:
        """It finished `ok` and produced something a reader can judge."""
        return self.ok and (bool(self.changes) or bool(self.final.strip()))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return _text(value)
    return value


def _view(
    label: str, port: str, final: str, trajectory: str, change_set: sn.Changes
) -> context.RolloutView:
    return context.RolloutView(
        label=label,
        ok=port == "ok",
        trajectory=trajectory,
        deliverable=sn.describe(change_set, final),
    )


def _reload(directory: Path, label: str, harness: Tree | None) -> Rollout | None:
    if not runmark.is_complete(directory, harness=harness):
        return None
    try:
        saved: Any = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        change_set = sn.load(directory / "changes")
        trajectory = (directory / "trajectory.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    port, final = str(saved["port"]), str(saved["final"])
    return Rollout(
        label=label,
        port=port,
        value=saved["value"],
        final=final,
        view=_view(label, port, final, trajectory, change_set),
        changes=change_set,
        cost=0.0,
        harness=harness,
        cached=True,
    )


async def run_rollout(
    env: Env,
    children: list[PipelineNode],
    input: PortValues,
    *,
    label: str,
    harness: Tree | None,
) -> Rollout:
    """Run the wrapped steps under `harness` (None: the item's own) and record the result."""
    directory = env.dir / "rollouts" / label
    reused = _reload(directory, label, harness)
    if reused is not None:
        env.log(f"rollout {label} reused")
        return reused
    env.check_budget()
    snap = sn.take(env.cwd, directory / "ws", exclude=env.cfg.exclude, copy_git=env.cfg.copy_git)
    if harness is not None:
        harness_tree.write_tree(env.surface, harness, snap.path, stubs=False)
        sn.refresh(snap)
    messages: list[dict[str, Any]] = []
    cost = [0.0]

    def tap(event: dict[str, Any]) -> None:
        if event.get("kind") == "agent.cost":
            cost[0] += float(event.get("usd", 0.0))

    scope = env.ctx.scope.child()
    scope.set("cwd", str(snap.path))
    scope.set("agent_tap", tap)
    scope.set("agent_raw_tap", messages.append)
    #: Replayed results belong to the item's own steps, never to a rollout's copies of them.
    scope.set("no_replay", True)
    async with env.gate:
        emit: Emit = await run_chain(children, input, env.ctx, scope)
    final = _text(emit.value)
    change_set = sn.changes(snap, skip=lambda rel: harness_tree.is_allowed(env.surface, rel))
    trajectory = tj.render(messages, final=final)
    env.charge(cost[0])
    directory.mkdir(parents=True, exist_ok=True)
    sn.save(change_set, directory / "changes")
    (directory / "trajectory.md").write_text(trajectory, encoding="utf-8")
    (directory / "result.json").write_text(
        json.dumps({"port": emit.port, "value": _json_safe(emit.value), "final": final}),
        encoding="utf-8",
    )
    runmark.mark_complete(
        directory, run_id=env.ctx.run_id, harness=harness, extra={"cost": cost[0]}
    )
    return Rollout(
        label=label,
        port=emit.port,
        value=emit.value,
        final=final,
        view=_view(label, emit.port, final, trajectory, change_set),
        changes=change_set,
        cost=cost[0],
        harness=harness,
    )
