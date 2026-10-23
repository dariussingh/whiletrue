from __future__ import annotations

from pydantic import BaseModel, PositiveInt

from whiletrue.basics.process import run_command, tail
from whiletrue.core import (
    ActionModule,
    ConfigIssue,
    Emit,
    Ports,
    PortSpec,
    StepContext,
    define_action,
)
from whiletrue.core.module import PortValues

#: git's well-known empty tree, so `diff` works in a repository without commits.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class DiffSizeConfig(BaseModel):
    max_lines: PositiveInt | None = None
    max_files: PositiveInt | None = None
    #: Revision to diff the working tree against. Default: HEAD.
    base: str = "HEAD"


def _validate(config: DiffSizeConfig, _vctx: object) -> list[ConfigIssue]:
    if config.max_lines is None and config.max_files is None:
        return [ConfigIssue("error", "`gate.diff_size` needs `max_lines` and/or `max_files`")]
    return []


async def _git(ctx: StepContext[DiffSizeConfig], *args: str):
    return await run_command("git", list(args), cwd=ctx.cwd, signal=ctx.signal)


async def _run(_input: PortValues, ctx: StepContext[DiffSizeConfig]) -> Emit:
    cfg = ctx.config
    base = cfg.base
    if base == "HEAD" and (await _git(ctx, "rev-parse", "--verify", "HEAD")).code != 0:
        base = _EMPTY_TREE
    diff = await _git(ctx, "diff", "--numstat", base)
    if diff.code != 0:
        return Emit("fail", {"error": "git diff failed", "output": tail(diff.stderr)})
    untracked = await _git(ctx, "ls-files", "--others", "--exclude-standard")
    if untracked.code != 0:
        return Emit("fail", {"error": "git ls-files failed", "output": tail(untracked.stderr)})
    lines = 0
    files = 0
    for row in diff.stdout.splitlines():
        added, deleted, _name = (row.split("\t", 2) + ["", "", ""])[:3]
        files += 1
        # Binary files show "-" for both counts.
        lines += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
    files += len([n for n in untracked.stdout.splitlines() if n])
    stats = {"lines": lines, "files": files}
    problems: list[str] = []
    if cfg.max_lines is not None and lines > cfg.max_lines:
        problems.append(f"{lines} changed lines exceed the limit of {cfg.max_lines}")
    if cfg.max_files is not None and files > cfg.max_files:
        problems.append(f"{files} changed files exceed the limit of {cfg.max_files}")
    if problems:
        return Emit("fail", {"error": "; ".join(problems), **stats})
    return Emit("ok", stats)


#: A deterministic gate: fails when the working tree differs from `base` by more than the limits
#: (changed lines and files, untracked files counted as files).
diff_size_module: ActionModule[DiffSizeConfig] = define_action(
    ActionModule(
        id="gate.diff_size",
        config=DiffSizeConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="deterministic",
        cacheable=False,
        validate=_validate,  # pyright: ignore[reportArgumentType]
        run=_run,
    )
)
