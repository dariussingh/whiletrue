"""Improvement sessions: propose, compose, select and reflect.

Each one is an agent run on a whiletrue runner, in a directory that holds an evidence tree. A
session edits files in place (propose and compose) or answers in text (select and reflect). Results
are cached on disk by a digest of everything the session could see, so a restart reuses finished
stages instead of paying for them again.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from whiletrue.abort import AbortSignal
from whiletrue.core import AgentLimits, AgentRequest, RunContext, parse_duration
from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import prompts as P
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.config import SessionSpec
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.library import Experience
from whiletrue.rsi.harness2.selection import (
    Selection,
    anon_id,
    assign_ids,
    is_base,
    parse_block,
    parse_choice,
)
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


def _digest(root: Path, extra: str) -> str:
    """A digest of every file the session could read, plus its prompt."""
    h = hashlib.sha256(extra.encode())
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(root).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def _load(workdir: Path, key: str) -> dict[str, Any] | None:
    try:
        data: Any = json.loads((workdir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    saved = cast("dict[str, Any]", data)
    return saved if saved.get("key") == key else None


def _store(workdir: Path, key: str, **fields: Any) -> None:
    (workdir / "result.json").write_text(
        json.dumps({"key": key, **fields}, default=str, sort_keys=True), encoding="utf-8"
    )


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
    key = _digest(workdir / "context", mission)
    hit = _load(workdir, key)
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
    _store(
        workdir,
        key,
        tree=result.tree,
        notes=result.notes,
        attempts=result.attempts,
        error=result.error,
    )
    return result


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
    key = _digest(workdir / "context", prompt)
    cached = _load(workdir, key)
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
        _store(workdir, key, reply=reply)
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


def _field_values(block: str) -> dict[str, str]:
    """`field: value` lines of an `<experience>` block; a value may run over several lines."""
    values: dict[str, list[str]] = {}
    current = ""
    for line in block.splitlines():
        match = re.match(rf"^\s*({'|'.join(_FIELDS)})\s*:\s*(.*)$", line)
        if match:
            current = match.group(1)
            values[current] = [match.group(2)]
        elif current:
            values[current].append(line)
    return {
        name: " ".join(part.strip() for part in lines if part.strip())
        for name, lines in values.items()
    }


def parse_experience(reply: str) -> Experience:
    """The `<experience>` block of a reflection. `NO_ENTRY` or a missing block is incomplete."""
    block = parse_block(reply, "experience")
    if not block or "NO_ENTRY" in reply.replace(block, ""):
        return Experience(edits=parse_block(reply, "edits"))
    values = _field_values(block)
    return Experience(
        cue=values.get("cue", ""),
        lesson=values.get("lesson", ""),
        evidence=values.get("evidence", ""),
        confidence=values.get("confidence", ""),
        edits=parse_block(reply, "edits"),
    )


async def reflect_stage(
    env: Env,
    *,
    base: Tree,
    bases: Sequence[context.RolloutView],
    pool: Sequence[context.PoolEntry],
    chosen_label: str,
    why: str,
) -> Experience:
    """Write what each edit did and at most one transferable lesson for the library."""
    workdir = env.dir / "stages" / "reflect"
    built = context.build(
        workdir / "context",
        env.surface,
        item_id=env.item_id,
        task=env.task,
        rollouts=bases,
        harness=base,
        library_dir=None,
        pool=pool,
        decisions={"selection": f"The submitted candidate was `{chosen_label}`.\n\n{why}".strip()},
    )
    mission = context.mission(built, job=P.experience(env.profile))
    (workdir / "MISSION.md").write_text(mission, encoding="utf-8")
    key = _digest(workdir / "context", mission)
    cached = _load(workdir, key)
    if cached is not None:
        return parse_experience(str(cached["reply"]))
    env.check_budget()
    spec = env.cfg.reflector_spec().model_copy(update={"tools": ["Read", "Glob", "Grep"]})
    outcome = await run_session(env, spec, prompt=mission, cwd=workdir, label="reflect")
    if not outcome.ok:
        env.log("reflection failed", {"error": outcome.error})
        return Experience()
    _store(workdir, key, reply=outcome.text)
    return parse_experience(outcome.text)
