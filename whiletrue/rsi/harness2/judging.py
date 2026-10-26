"""Judging sessions: a blind selector among finals, and a reviewer for adoption."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import prompts as P
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.selection import (
    Selection,
    anon_id,
    assign_ids,
    is_base,
    parse_block,
    parse_choice,
)
from whiletrue.rsi.harness2.sessions import run_session
from whiletrue.rsi.harness2.stagecache import digest, load_cached, store_cached
from whiletrue.rsi.harness2.tree import Tree


def _write_candidates(root: Path, candidates: Mapping[str, str], id_map: Mapping[str, str]) -> None:
    folder = root / "context" / "candidates"
    folder.mkdir(parents=True, exist_ok=True)
    for anon, label in id_map.items():
        (folder / f"{anon}.md").write_text(candidates[label], encoding="utf-8")


async def select_stage(env: Env, finals: Mapping[str, str], bases: Mapping[str, str]) -> Selection:
    """Pick one final output, blind. A selector failure falls back to a delivered base."""
    selection = Selection(
        item_id=env.item_id, mode=env.cfg.mode, k=env.cfg.k, n_candidates=len(finals)
    )
    if not finals:
        selection.error = "no final produced a deliverable"
        return _fallback(selection, bases)
    if len(finals) == 1:
        label = next(iter(finals))
        selection.chosen_id, selection.chosen_label = "A", label
        selection.id_map, selection.ok, selection.trivial = {"A": label}, True, True
        return selection
    selection.id_map = assign_ids(list(finals), item_id=env.item_id, k=env.cfg.k, mode=env.cfg.mode)
    workdir = env.dir / "stages" / "select"
    shutil.rmtree(workdir / "context", ignore_errors=True)
    (workdir / "context").mkdir(parents=True)
    (workdir / "context" / "task.md").write_text(env.task, encoding="utf-8")
    _write_candidates(workdir, finals, selection.id_map)
    prompt = P.select(env.profile, len(finals))
    key = digest(workdir / "context", prompt)
    cached = load_cached(workdir, key)
    if cached is not None:
        reply, cost = str(cached["reply"]), 0.0
    else:
        env.check_budget()
        spec = env.cfg.selector_spec()
        read_only = spec.model_copy(update={"tools": ["Read", "Glob", "Grep"]})
        outcome = await run_session(env, read_only, prompt=prompt, cwd=workdir, label="select")
        reply, cost = outcome.text, outcome.cost
        if not outcome.ok:
            selection.error = f"selector failed: {outcome.error}"
            selection.usd = cost
            return _fallback(selection, bases)
        store_cached(workdir, key, reply=reply)
    selection.usd = cost
    choice = parse_choice(reply, list(selection.id_map))
    if choice is None:
        selection.error = "selector gave no valid choice"
        return _fallback(selection, bases)
    selection.chosen_id, selection.chosen_label = choice, selection.id_map[choice]
    selection.why = parse_block(reply, "why")
    selection.ok = True
    return selection


def _fallback(selection: Selection, bases: Mapping[str, str]) -> Selection:
    """Deliver a base output when selection cannot pick; never raise."""
    selection.fallback = True
    delivered = sorted(label for label in bases if is_base(label))
    if delivered:
        selection.chosen_label = delivered[0]
        selection.chosen_is_base = True
        selection.chosen_id = anon_id(0)
    return selection


@dataclass
class ReviewResult:
    approved: bool
    why: str = ""
    error: str = ""


async def review_stage(
    env: Env,
    *,
    base: Tree,
    bases: Sequence[context.RolloutView],
    chosen: context.PoolEntry,
    why: str,
    edits: str,
) -> ReviewResult:
    """A model decides whether the proposed harness should be adopted.

    It fails closed: a failed session, or any answer that is not exactly `approve`, rejects.
    """
    workdir = env.dir / "stages" / "review"
    notes = f"Chosen candidate: `{chosen.label}`.\n\nWhy it was chosen:\n{why or '(trivial pick)'}"
    if edits.strip():
        notes += f"\n\nWhat each edit did (from the reflection):\n{edits.strip()}"
    built = context.build(
        workdir / "context",
        env.surface,
        item_id=env.item_id,
        task=env.task,
        rollouts=bases,
        harness=base,
        pool=[chosen],
        decisions={"selection": notes},
    )
    mission = context.mission(built, job=P.review(env.profile))
    (workdir / "MISSION.md").write_text(mission, encoding="utf-8")
    key = digest(workdir / "context", mission)
    cached = load_cached(workdir, key)
    if cached is not None:
        reply = str(cached["reply"])
    else:
        env.check_budget()
        spec = env.cfg.reviewer_spec().model_copy(update={"tools": ["Read", "Glob", "Grep"]})
        outcome = await run_session(env, spec, prompt=mission, cwd=workdir, label="review")
        if not outcome.ok:
            return ReviewResult(False, error=f"reviewer failed: {outcome.error}")
        reply = outcome.text
        store_cached(workdir, key, reply=reply)
    decision = parse_block(reply, "decision").strip().lower()
    return ReviewResult(decision == "approve", parse_block(reply, "why"))
