"""Improvement sessions: propose, compose, select and reflect.

Each one is an agent run on a whiletrue runner, in a directory that holds an evidence tree. A
session edits files in place (propose and compose) or answers in text (select and reflect). Results
are cached on disk by a digest of everything the session could see, so a restart reuses finished
stages instead of paying for them again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from whiletrue.abort import AbortSignal
from whiletrue.core import AgentLimits, AgentRequest, RunContext, parse_duration
from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.config import SessionSpec
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.stagecache import digest, load_cached, store_cached
from whiletrue.rsi.harness2.tree import Tree, TreeError

#: A proposal that fails validation gets this many tries before it inherits the current harness.
MAX_ATTEMPTS = 2
_FIELDS = ("cue", "lesson", "evidence", "confidence")


@dataclass
class SessionOutcome:
    ok: bool
    text: str = ""
    cost: float = 0.0
    error: str = ""


@dataclass
class EditResult:
    """What a propose or compose session produced."""

    tree: Tree
    notes: str = ""
    cost: float = 0.0
    attempts: int = 0
    error: str = ""
    cached: bool = False


async def run_session(
    env: Env, spec: SessionSpec, *, prompt: str, cwd: Path, label: str
) -> SessionOutcome:
    """Run one session on its runner and charge its cost."""
    ctx = env.ctx
    runner = ctx.registry.get_runner(spec.runner)
    if runner is None:
        return SessionOutcome(False, error=f'unknown runner "{spec.runner}"')
    wall_ms = parse_duration(spec.limits.wall) if spec.limits.wall is not None else None
    budget = spec.limits.budget_usd
    budget = min(budget, env.remaining) if budget is not None else env.remaining
    limits = AgentLimits(
        budget_usd=budget if runner.capabilities.budget and budget > 0 else None,
        turns=spec.limits.turns,
        wall_ms=wall_ms,
    )
    request = AgentRequest(
        prompt=prompt,
        cwd=str(cwd),
        model=spec.model,
        allowed_tools=list(spec.tools),
        permission_mode=spec.permission_mode,
        limits=limits,
        step_key=f"{ctx.step_key}:{label}",
        label=f"{ctx.item_id} rsi {label}",
    )

    def on_event(event: dict[str, Any]) -> None:
        ctx.bus.emit(
            "agent.event",
            ctx.run_id,
            {"node": ctx.node_id, "rsi": label, **event},
            item_id=ctx.item_id,
        )

    wall = AbortSignal.timeout(wall_ms) if wall_ms is not None else None
    signal = AbortSignal.any(ctx.signal, wall) if wall is not None else ctx.signal
    try:
        async with env.gate:
            if ctx.agent_slots is not None:
                async with ctx.agent_slots:
                    result = await runner.run(request, RunContext(signal, on_event))
            else:
                result = await runner.run(request, RunContext(signal, on_event))
    finally:
        if wall is not None:
            wall.dispose()
            signal.dispose()
    cost = result.cost_usd or 0.0
    if cost:
        ctx.record_cost(cost)
        env.charge(cost)
    if not result.ok:
        return SessionOutcome(False, result.text, cost, result.error or result.park or "failed")
    return SessionOutcome(True, result.text, cost)


async def edit_stage(
    env: Env,
    *,
    label: str,
    job: str,
    harness: Tree,
    rollouts: Sequence[context.RolloutView],
    pool: Sequence[context.PoolEntry] = (),
    decisions: Mapping[str, str] | None = None,
) -> EditResult:
    """One propose or compose session: build evidence, let the improver edit, validate the result.

    A harness that fails validation gets one retry with the problems listed; after that, or when
    the session fails, the stage inherits `harness` unchanged and says why.
    """
    workdir = env.dir / "stages" / label
    built = context.build(
        workdir / "context",
        env.surface,
        item_id=env.item_id,
        task=env.task,
        rollouts=rollouts,
        harness=harness,
        library_dir=env.library.context_dir(),
        pool=pool,
        decisions=decisions,
    )
    mission = context.mission(built, job=job)
    (workdir / "MISSION.md").write_text(mission, encoding="utf-8")
    key = digest(workdir / "context", mission)
    hit = load_cached(workdir, key)
    if hit is not None:
        saved = cast("dict[Any, Any]", hit["tree"])
        return EditResult(
            tree={str(k): str(v) for k, v in saved.items()},
            notes=str(hit.get("notes", "")),
            cost=0.0,
            attempts=int(hit.get("attempts", 1)),
            error=str(hit.get("error", "")),
            cached=True,
        )
    env.check_budget()
    result = EditResult(tree=dict(harness))
    prompt = mission
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome = await run_session(env, env.cfg.improver, prompt=prompt, cwd=workdir, label=label)
        result.cost += outcome.cost
        result.attempts = attempt
        if not outcome.ok:
            result.error = f"session failed: {outcome.error}"
            break
        proposed = harness_tree.read_tree(env.surface, workdir / "context" / "harness")
        harness_tree.autotrigger(env.surface, proposed)
        try:
            harness_tree.validate(env.surface, proposed)
            context.assert_tree_has_no_verdicts(proposed)
        except (TreeError, context.LeakError) as error:
            result.error = f"harness rejected: {error}"
            prompt = (
                f"{mission}\n\n# VALIDATION FAILED\n\nYour harness was rejected: {error}\n"
                "Fix every problem in `context/harness/` in place, then reply with your "
                "`<notes>` block."
            )
            continue
        result.tree, result.notes, result.error = proposed, outcome.text, ""
        break
    if result.error:
        env.log(f"{label} inherits the current harness", {"reason": result.error})
    store_cached(
        workdir,
        key,
        tree=result.tree,
        notes=result.notes,
        attempts=result.attempts,
        error=result.error,
    )
    return result
