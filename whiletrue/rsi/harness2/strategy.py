"""The `rsi.harness2` strategy: improve the harness around the steps it wraps.

For one item it runs the stages of a mode (`modes.py`), selects one result blind, applies the
chosen rollout's file changes to the item's workspace so later steps (gates, commit, PR) run on
them, reflects on what happened, and saves the chosen harness as a proposal for adoption
(`adopt.py`). Static checks are in `checks.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from whiletrue.control.chain import run_chain
from whiletrue.core import (
    CompositeContext,
    Emit,
    PipelineNode,
    Strategy,
    define_strategy,
    parse_expr,
)
from whiletrue.core.module import PortValues
from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import snapshot as sn
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.checks import validate, validate_node
from whiletrue.rsi.harness2.config import Harness2Config
from whiletrue.rsi.harness2.env import BudgetStop, Env
from whiletrue.rsi.harness2.judging import review_stage, select_stage
from whiletrue.rsi.harness2.ledger import BudgetError, Ledger
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.modes import Outcome, run_parallel, run_sequential
from whiletrue.rsi.harness2.reflection import reflect_stage
from whiletrue.rsi.harness2.rollout import Rollout
from whiletrue.rsi.harness2.surfaces import SURFACES
from whiletrue.rsi.harness2.tree import Tree


def _task_text(item: object, input: PortValues) -> str:
    """The request the agent was given, as the improver sees it."""
    parts: list[str] = []
    if isinstance(item, dict):
        fields = {str(k): v for k, v in cast("dict[Any, Any]", item).items()}
        if fields.get("title"):
            parts.append(f"# {fields['title']}")
        if fields.get("body"):
            parts.append(str(fields["body"]))
        if not parts:
            parts.append("\n".join(f"{k}: {v}" for k, v in fields.items()))
    value = input.get("in")
    if isinstance(value, str) and value.strip():
        parts.append(f"## Input to the steps\n{value.strip()}")
    return "\n\n".join(parts) or "(the item carries no description)"


def _make_env(ctx: CompositeContext[Harness2Config], input: PortValues) -> Env:
    cfg = ctx.config
    project = Path(str(ctx.scope.get("project_dir") or ctx.cwd))
    surface = SURFACES[cfg.surface]
    domain = cfg.domain.strip() or project.name
    return Env(
        ctx=ctx,
        cfg=cfg,
        surface=surface,
        profile=cfg.profile.build(),
        task=_task_text(ctx.scope.get("item"), input),
        cwd=Path(ctx.cwd),
        dir=proposals.item_dir(project, ctx.item_id),
        library=Library(project / cfg.library, domain, surface),
        ledger=Ledger(cfg.mode, ctx.item_id, cfg.k),
        gate=asyncio.Semaphore(cfg.concurrency),
    )


def _apply(env: Env, chosen: Rollout) -> list[str]:
    """Write the chosen rollout's file changes into the item's own workspace."""
    return sn.apply(chosen.changes, env.cwd)


async def _finish(env: Env, outcome: Outcome, base: Tree) -> Emit:
    """Select, apply the result, reflect, and save the proposal."""
    delivered_finals = {lb: r.view.deliverable for lb, r in outcome.finals.items() if r.delivered}
    delivered_bases = {lb: r.view.deliverable for lb, r in outcome.bases.items() if r.delivered}
    selection = await select_stage(env, delivered_finals, delivered_bases)
    by_label: dict[str, Rollout] = {**outcome.bases, **outcome.finals}
    chosen = by_label.get(selection.chosen_label)
    if chosen is None or not chosen.delivered:
        last = next(reversed(by_label.values()), None)
        return Emit(
            "fail",
            {
                "error": "rsi: no rollout produced a deliverable",
                "last": last.value if last is not None else None,
            },
        )
    touched = _apply(env, chosen)
    env.log(
        "applied the chosen rollout",
        {"label": chosen.label, "files": touched, "selection": selection.why},
    )
    chosen_tree = outcome.trees.get(chosen.label, base)
    experience = await reflect_stage(
        env,
        base=base,
        bases=[r.view for r in outcome.bases.values()],
        pool=outcome.pool,
        chosen_label=chosen.label,
        why=selection.why,
    )
    project = Path(str(env.ctx.scope.get("project_dir") or env.ctx.cwd))
    proposal = proposals.make(
        item_id=env.item_id,
        domain=env.library.domain,
        surface_name=env.surface.name,
        base=base,
        chosen=chosen_tree,
        chosen_label=chosen.label,
        restricted=harness_tree.restricted_changes(env.surface, base, chosen_tree),
        experience=experience,
        why=selection.why,
        adopt=env.cfg.adopt,
        ledger=env.ledger.summary() | {"spent": round(env.spent, 6)},
        run_id=env.ctx.run_id,
        library=str(env.library.root),
    )
    if env.cfg.adopt == "model" and proposal.changed:
        verdict = await review_stage(
            env,
            base=base,
            bases=[r.view for r in outcome.bases.values()],
            chosen=context.PoolEntry(chosen.label, chosen_tree, chosen.view),
            why=selection.why,
            edits=experience.edits,
        )
        proposal.model_decision = "approve" if verdict.approved else "reject"
        proposal.model_why = verdict.why or verdict.error
        proposal.ledger["spent"] = round(env.spent, 6)
    proposals.save(project, proposal)
    try:
        env.ledger.assert_identity()
    except BudgetError as error:
        env.log("budget identity broken", {"detail": str(error)})
    return Emit(chosen.port, chosen.value)


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[Harness2Config]
) -> Emit:
    cfg = ctx.config
    if cfg.when is not None:
        scope = {**ctx.scope.snapshot(), "input": input.get("in")}
        if not parse_expr(cfg.when).eval_bool(scope):
            ctx.log("info", "rsi skipped: `when` is false", None)
            return await run_chain(children, input, ctx)
    env = _make_env(ctx, input)
    base = harness_tree.read_tree(env.surface, env.cwd)
    try:
        mode = run_sequential if cfg.mode == "sequential" else run_parallel
        outcome = await mode(env, children, input, base)
        return await _finish(env, outcome, base)
    except BudgetStop as stop:
        return Emit("needs_human", {"reason": "rsi_budget_exceeded", "detail": str(stop)})
    except (sn.SnapshotError, OSError) as error:
        return Emit("fail", {"error": f"rsi: {error}"})


rsi_harness2_strategy: Strategy[Harness2Config] = define_strategy(
    Strategy(
        id="rsi.harness2",
        config=Harness2Config,
        run=_run,
        chain=False,
        validate=validate,
        validate_node=validate_node,
    )
)

__all__ = ["rsi_harness2_strategy"]
