"""The two recursion modes: the stages of one item, from reference rollouts to final harnesses.

Parallel: k general and k domain candidates from the same evidence, then k independent
compositions. Sequential: k rounds, each editing the previous checkpoint. Both spend R = 4k
rollouts and P = 3k sessions. Stage order follows Harness² (`core/recursion.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from whiletrue.core import PipelineNode
from whiletrue.core.module import PortValues
from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import prompts as P
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.rollout import Rollout, run_rollout
from whiletrue.rsi.harness2.sessions import EditResult, edit_stage
from whiletrue.rsi.harness2.tree import Tree


def record(env: Env, rollouts: Sequence[Rollout]) -> None:
    for rollout in rollouts:
        env.ledger.rollout(rollout.label, rollout.cost, cached=rollout.cached)


def record_sessions(env: Env, edits: dict[str, EditResult]) -> None:
    for label, edit in edits.items():
        env.ledger.session(label, edit.cost, cached=edit.cached, attempts=max(edit.attempts, 1))


async def edits(env: Env, jobs: dict[str, dict[str, object]]) -> dict[str, EditResult]:
    """Run several propose or compose sessions at once."""
    labels = list(jobs)
    results = await asyncio.gather(*(edit_stage(env, label=lb, **jobs[lb]) for lb in labels))  # pyright: ignore[reportArgumentType]
    out = dict(zip(labels, results, strict=True))
    record_sessions(env, out)
    return out


async def rollouts(
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
    record(env, list(out.values()))
    return out


class Outcome:
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


async def run_parallel(
    env: Env, children: list[PipelineNode], input: PortValues, base: Tree
) -> Outcome:
    k, profile, surface = env.cfg.k, env.profile, env.surface
    bases = await rollouts(env, children, input, {f"base{i}": None for i in range(1, k + 1)})
    base_views = [r.view for r in bases.values()]
    jobs: dict[str, dict[str, object]] = {}
    for i in range(1, k + 1):
        for dim, prefix in (("INV", "inv"), ("DOM", "dom")):
            jobs[f"{prefix}{i}"] = {
                "job": P.propose(profile, dim, surface, k=k, index=i),
                "harness": base,
                "rollouts": base_views,
            }
    proposed = await edits(env, jobs)
    probed = await rollouts(env, children, input, {lb: e.tree for lb, e in proposed.items()})
    pool = [context.PoolEntry(lb, proposed[lb].tree, probed[lb].view) for lb in proposed]
    composed = await edits(
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
    finals = await rollouts(env, children, input, {lb: e.tree for lb, e in composed.items()})
    all_pool = [
        *pool,
        *(context.PoolEntry(lb, composed[lb].tree, finals[lb].view) for lb in composed),
    ]
    return Outcome(bases, finals, {lb: e.tree for lb, e in composed.items()}, all_pool)


async def run_sequential(
    env: Env, children: list[PipelineNode], input: PortValues, base: Tree
) -> Outcome:
    k, profile, surface = env.cfg.k, env.profile, env.surface
    bases = await rollouts(env, children, input, {f"base{i}": None for i in range(1, k + 1)})
    evidence = [r.view for r in bases.values()]
    current = base
    pool: list[context.PoolEntry] = []
    finals: dict[str, Rollout] = {}
    trees: dict[str, Tree] = {}
    decisions: dict[str, str] = {}
    for t in range(1, k + 1):
        proposed = await edits(
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
        probed = await rollouts(env, children, input, {lb: e.tree for lb, e in proposed.items()})
        pool += [context.PoolEntry(lb, proposed[lb].tree, probed[lb].view) for lb in proposed]
        label = f"c{t}"
        composed = (
            await edits(
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
        final = (await rollouts(env, children, input, {label: composed.tree}))[label]
        decisions[label] = composed.notes
        evidence.append(final.view)
        pool.append(context.PoolEntry(label, composed.tree, final.view))
        finals[label], trees[label], current = final, composed.tree, composed.tree
    return Outcome(bases, finals, trees, pool)
