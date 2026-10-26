"""The `rsi.harness2` strategy: improve the harness around the steps it wraps.

For one item it runs the wrapped steps in isolated copies of the workspace, lets improver sessions
edit the harness (the files an agent framework loads), probes the edits by running the steps
again, composes refined harnesses from the contrasts, and selects one result blind. The chosen
rollout's file changes are applied to the item's workspace so later steps (gates, commit, PR) run
on them; the chosen harness is saved as a proposal for a person to adopt.

Two modes spend the same budget (R = 4k rollouts, P = 3k sessions). Parallel: k general and k
domain candidates from the same evidence, then k independent compositions. Sequential: k rounds,
each editing the previous checkpoint. Stage order follows Harness² (`core/recursion.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from whiletrue.control.chain import run_chain
from whiletrue.core import (
    CompositeContext,
    ConfigIssue,
    Emit,
    PipelineNode,
    Strategy,
    ValidateContext,
    define_strategy,
    load_agent_definition,
    parse_expr,
)
from whiletrue.core.module import PortValues
from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import prompts as P
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import snapshot as sn
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.config import Harness2Config
from whiletrue.rsi.harness2.env import BudgetStop, Env
from whiletrue.rsi.harness2.ledger import BudgetError, Ledger
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.rollout import Rollout, run_rollout
from whiletrue.rsi.harness2.sessions import EditResult, edit_stage, reflect_stage, select_stage
from whiletrue.rsi.harness2.surfaces import SURFACE_FOR_RUNNER, SURFACES
from whiletrue.rsi.harness2.tree import Tree

#: Variables a `when:` expression may use.
WHEN_VARIABLES = frozenset({"item", "iter", "run", "input", "project_dir"})


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


def _record(env: Env, rollouts: Sequence[Rollout]) -> None:
    for rollout in rollouts:
        env.ledger.rollout(rollout.label, rollout.cost, cached=rollout.cached)


def _record_sessions(env: Env, edits: dict[str, EditResult]) -> None:
    for label, edit in edits.items():
        env.ledger.session(label, edit.cost, cached=edit.cached, attempts=max(edit.attempts, 1))


async def _edits(env: Env, jobs: dict[str, dict[str, object]]) -> dict[str, EditResult]:
    """Run several propose or compose sessions at once."""
    labels = list(jobs)
    results = await asyncio.gather(*(edit_stage(env, label=lb, **jobs[lb]) for lb in labels))  # pyright: ignore[reportArgumentType]
    out = dict(zip(labels, results, strict=True))
    _record_sessions(env, out)
    return out


async def _rollouts(
    env: Env,
    children: list[PipelineNode],
    input: PortValues,
    runs: dict[str, Tree | None],
) -> dict[str, Rollout]:
    labels = list(runs)
    results = await asyncio.gather(
        *(run_rollout(env, children, input, label=lb, harness=runs[lb]) for lb in labels)
    )
    out = dict(zip(labels, results, strict=True))
    _record(env, list(out.values()))
    return out


class _Outcome:
    """What a mode hands to the shared finish step."""

    def __init__(
        self,
        bases: dict[str, Rollout],
        finals: dict[str, Rollout],
        trees: dict[str, Tree],
        pool: list[context.PoolEntry],
    ) -> None:
        self.bases = bases
        self.finals = finals
        #: Final label to the harness that produced it.
        self.trees = trees
        self.pool = pool


async def _parallel(
    env: Env, children: list[PipelineNode], input: PortValues, base: Tree
) -> _Outcome:
    k, profile, surface = env.cfg.k, env.profile, env.surface
    bases = await _rollouts(env, children, input, {f"base{i}": None for i in range(1, k + 1)})
    base_views = [r.view for r in bases.values()]
    jobs: dict[str, dict[str, object]] = {}
    for i in range(1, k + 1):
        for dim, prefix in (("INV", "inv"), ("DOM", "dom")):
            jobs[f"{prefix}{i}"] = {
                "job": P.propose(profile, dim, surface, k=k, index=i),
                "harness": base,
                "rollouts": base_views,
            }
    proposed = await _edits(env, jobs)
    probed = await _rollouts(env, children, input, {lb: e.tree for lb, e in proposed.items()})
    pool = [context.PoolEntry(lb, proposed[lb].tree, probed[lb].view) for lb in proposed]
    composed = await _edits(
        env,
        {
            f"agg{i}": {
                "job": P.aggregate(profile, surface, k=k, index=i),
                "harness": base,
                "rollouts": base_views,
                "pool": pool,
            }
            for i in range(1, k + 1)
        },
    )
    finals = await _rollouts(env, children, input, {lb: e.tree for lb, e in composed.items()})
    all_pool = [
        *pool,
        *(context.PoolEntry(lb, composed[lb].tree, finals[lb].view) for lb in composed),
    ]
    return _Outcome(bases, finals, {lb: e.tree for lb, e in composed.items()}, all_pool)


async def _sequential(
    env: Env, children: list[PipelineNode], input: PortValues, base: Tree
) -> _Outcome:
    k, profile, surface = env.cfg.k, env.profile, env.surface
    bases = await _rollouts(env, children, input, {f"base{i}": None for i in range(1, k + 1)})
    evidence = [r.view for r in bases.values()]
    current = base
    pool: list[context.PoolEntry] = []
    finals: dict[str, Rollout] = {}
    trees: dict[str, Tree] = {}
    decisions: dict[str, str] = {}
    for t in range(1, k + 1):
        proposed = await _edits(
            env,
            {
                f"{prefix}{t}": {
                    "job": P.propose(profile, dim, surface, k=k, index=1, round_t=t),
                    "harness": current,
                    "rollouts": evidence,
                    "decisions": dict(decisions),
                }
                for dim, prefix in (("INV", "inv"), ("DOM", "dom"))
            },
        )
        probed = await _rollouts(env, children, input, {lb: e.tree for lb, e in proposed.items()})
        pool += [context.PoolEntry(lb, proposed[lb].tree, probed[lb].view) for lb in proposed]
        label = f"c{t}"
        composed = (
            await _edits(
                env,
                {
                    label: {
                        "job": P.compose_round(profile, surface, t=t, k=k),
                        "harness": current,
                        "rollouts": evidence,
                        "pool": list(pool),
                        "decisions": dict(decisions),
                    }
                },
            )
        )[label]
        final = (await _rollouts(env, children, input, {label: composed.tree}))[label]
        decisions[label] = composed.notes
        evidence.append(final.view)
        pool.append(context.PoolEntry(label, composed.tree, final.view))
        finals[label], trees[label], current = final, composed.tree, composed.tree
    return _Outcome(bases, finals, trees, pool)


def _apply(env: Env, chosen: Rollout) -> list[str]:
    """Write the chosen rollout's file changes into the item's own workspace."""
    return sn.apply(chosen.changes, env.cwd)


async def _finish(env: Env, outcome: _Outcome, base: Tree) -> Emit:
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
    proposals.save(
        project,
        proposals.make(
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
        ),
    )
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
        mode = _sequential if cfg.mode == "sequential" else _parallel
        outcome = await mode(env, children, input, base)
        return await _finish(env, outcome, base)
    except BudgetStop as stop:
        return Emit("needs_human", {"reason": "rsi_budget_exceeded", "detail": str(stop)})
    except (sn.SnapshotError, OSError) as error:
        return Emit("fail", {"error": f"rsi: {error}"})


# ---- static checks -----------------------------------------------------------------------


def _walk(node: PipelineNode) -> list[PipelineNode]:
    return [node, *(n for child in node.children for n in _walk(child))]


def _surface_for(node: PipelineNode, cwd: str) -> tuple[str | None, str | None]:
    """(runner id, problem) for an agent step."""
    uses = node.config.get("uses")
    runner: object = node.config.get("runner")
    if not isinstance(runner, str):
        try:
            runner = load_agent_definition(Path(cwd) / str(uses)).runner
        except Exception as error:  # a bad agent file is reported by the agent step itself
            return None, f"cannot read agent file {uses}: {error}"
    return str(runner), None


def _check_children(
    cfg: Harness2Config, node: PipelineNode, vctx: ValidateContext
) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    steps = [n for child in node.children for n in _walk(child)]
    agents = [n for n in steps if n.use == "agent"]
    if not agents:
        issues.append(
            ConfigIssue("error", "`rsi.harness2` wraps no agent step: nothing to improve")
        )
    for step in steps:
        resolved = vctx.registry.resolve(step.use)
        if resolved is None:
            continue
        if resolved.kind == "action" and resolved.module.effects == "side-effect":
            issues.append(
                ConfigIssue(
                    "error",
                    f"`{step.use}` has side effects and cannot run inside `rsi.harness2`: it "
                    "would repeat in every rollout. Move it after the rsi step.",
                )
            )
        if step.use.startswith("human."):
            issues.append(
                ConfigIssue(
                    "error",
                    f"`{step.use}` cannot run inside `rsi.harness2` (a person "
                    "would be asked in every rollout)",
                )
            )
    for step in agents:
        runner, problem = _surface_for(step, vctx.cwd)
        if problem:
            continue
        mapped = SURFACE_FOR_RUNNER.get(runner or "")
        if mapped is None:
            known = ", ".join(sorted(SURFACE_FOR_RUNNER))
            issues.append(
                ConfigIssue(
                    "error",
                    f'Cannot improve the harness of runner "{runner}" yet (supported: {known})',
                )
            )
        elif mapped != cfg.surface:
            issues.append(
                ConfigIssue(
                    "warning",
                    f'Agent step "{step.id}" runs on "{runner}" but `rsi.surface` is '
                    f'"{cfg.surface}": the edits will not reach it',
                )
            )
    return issues


def _validate(cfg: Harness2Config, vctx: ValidateContext) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for name, spec in (
        ("improver", cfg.improver),
        ("selector", cfg.selector_spec()),
        ("reflector", cfg.reflector_spec()),
    ):
        if vctx.registry.get_runner(spec.runner) is None:
            issues.append(ConfigIssue("error", f'`rsi.{name}`: unknown runner "{spec.runner}"'))
    if cfg.when is not None:
        try:
            problems = parse_expr(cfg.when).check(WHEN_VARIABLES)
        except ValueError as error:
            problems = [str(error)]
        issues += [ConfigIssue("error", f"`rsi.when`: {p}") for p in problems]
    if cfg.budget_is_default:
        issues.append(
            ConfigIssue(
                "warning",
                f"`rsi` sets no budget, so each item may spend up to ${cfg.budget_usd:.2f}; "
                "set `rsi.budget_usd` to choose",
            )
        )
    rollouts, sessions = 4 * cfg.k, 3 * cfg.k
    issues.append(
        ConfigIssue(
            "warning",
            f"rsi runs {rollouts} rollouts and {sessions} improvement sessions per item at "
            f"k={cfg.k} (about {rollouts + sessions}x one agent run); spend is capped at "
            f"${cfg.budget_usd:.2f} per item",
        )
    )
    return issues


def _validate_node(
    cfg: Harness2Config, node: PipelineNode, vctx: ValidateContext
) -> list[ConfigIssue]:
    return _check_children(cfg, node, vctx)


rsi_harness2_strategy: Strategy[Harness2Config] = define_strategy(
    Strategy(
        id="rsi.harness2",
        config=Harness2Config,
        run=_run,
        chain=False,
        validate=_validate,
        validate_node=_validate_node,
    )
)

__all__ = ["rsi_harness2_strategy"]
