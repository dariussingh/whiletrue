"""Reflection: what each edit did, and the one lesson worth keeping."""

from __future__ import annotations

import re
from collections.abc import Sequence

from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import prompts as P
from whiletrue.rsi.harness2.env import Env
from whiletrue.rsi.harness2.library import Experience
from whiletrue.rsi.harness2.selection import parse_block
from whiletrue.rsi.harness2.sessions import run_session
from whiletrue.rsi.harness2.stagecache import digest, load_cached, store_cached
from whiletrue.rsi.harness2.tree import Tree

_FIELDS = ("cue", "lesson", "evidence", "confidence")


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
    key = digest(workdir / "context", mission)
    cached = load_cached(workdir, key)
    if cached is not None:
        return parse_experience(str(cached["reply"]))
    env.check_budget()
    spec = env.cfg.reflector_spec().model_copy(update={"tools": ["Read", "Glob", "Grep"]})
    outcome = await run_session(env, spec, prompt=mission, cwd=workdir, label="reflect")
    if not outcome.ok:
        env.log("reflection failed", {"error": outcome.error})
        return Experience()
    store_cached(workdir, key, reply=outcome.text)
    return parse_experience(outcome.text)
