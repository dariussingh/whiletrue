from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from whiletrue.basics.process import run_command, tail
from whiletrue.core import (
    ActionModule,
    Emit,
    Ports,
    PortSpec,
    StepContext,
    define_action,
)
from whiletrue.core.module import PortValues


class CommitConfig(BaseModel):
    message: str = Field(min_length=1)
    #: Paths to stage (`.` stages everything, including deletions).
    add: list[str] = Field(default_factory=lambda: ["."])
    #: Commit even when nothing changed.
    allow_empty: bool = False


class CommitPushConfig(CommitConfig):
    remote: str = "origin"


class DiffConfig(BaseModel):
    base: str = "HEAD"
    #: Include the unified diff text in the output.
    patch: bool = False


async def _git(ctx: StepContext[Any], *args: str) -> tuple[int | None, str, str]:
    result = await run_command("git", list(args), cwd=ctx.cwd, signal=ctx.signal)
    return result.code, result.stdout, result.stderr


async def _commit(ctx: StepContext[Any], cfg: CommitConfig) -> Emit:
    code, _, err = await _git(ctx, "add", "-A", "--", *cfg.add)
    if code != 0:
        return Emit("fail", {"error": f"git add failed: {tail(err)}"})
    staged, _, _ = await _git(ctx, "diff", "--cached", "--quiet")
    if staged == 0 and not cfg.allow_empty:
        return Emit(
            "ok", {"committed": False, "sha": (await _git(ctx, "rev-parse", "HEAD"))[1].strip()}
        )
    args = ["commit", "-m", cfg.message]
    if cfg.allow_empty:
        args.append("--allow-empty")
    code, out, err = await _git(ctx, *args)
    if code != 0:
        return Emit("fail", {"error": f"git commit failed: {tail(err or out)}"})
    sha = (await _git(ctx, "rev-parse", "HEAD"))[1].strip()
    return Emit("ok", {"committed": True, "sha": sha})


async def _commit_run(input: PortValues, ctx: StepContext[CommitConfig]) -> Emit:
    return await _commit(ctx, ctx.config)


async def _commit_push_run(input: PortValues, ctx: StepContext[CommitPushConfig]) -> Emit:
    made = await _commit(ctx, ctx.config)
    if made.port != "ok":
        return made
    code, out, err = await _git(ctx, "push", "-u", ctx.config.remote, "HEAD")
    if code != 0:
        return Emit("fail", {"error": f"git push failed: {tail(err or out)}"})
    return Emit("ok", {**made.value, "pushed": True})


async def _diff_run(input: PortValues, ctx: StepContext[DiffConfig]) -> Emit:
    code, out, err = await _git(ctx, "diff", "--numstat", ctx.config.base)
    if code != 0:
        return Emit("fail", {"error": f"git diff failed: {tail(err)}"})
    files: list[dict[str, Any]] = []
    for line in out.splitlines():
        added, removed, path = line.split("\t", 2)
        files.append(
            {
                "path": path,
                "additions": 0 if added == "-" else int(added),
                "deletions": 0 if removed == "-" else int(removed),
            }
        )
    value: dict[str, Any] = {"files": files}
    if ctx.config.patch:
        value["patch"] = (await _git(ctx, "diff", ctx.config.base))[1]
    return Emit("ok", value)


_ANY = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])

git_commit_module: ActionModule[CommitConfig] = define_action(
    ActionModule(
        id="git.commit",
        config=CommitConfig,
        ports=_ANY,
        effects="side-effect",
        cacheable=False,
        idempotency_key=lambda _i, ctx: f"git.commit:{ctx.step_key}",
        run=_commit_run,
    )
)
git_commit_push_module: ActionModule[CommitPushConfig] = define_action(
    ActionModule(
        id="git.commit_push",
        config=CommitPushConfig,
        ports=_ANY,
        effects="side-effect",
        cacheable=False,
        idempotency_key=lambda _i, ctx: f"git.commit_push:{ctx.step_key}",
        run=_commit_push_run,
    )
)
git_diff_module: ActionModule[DiffConfig] = define_action(
    ActionModule(
        id="git.diff",
        config=DiffConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Diff")]),
        effects="deterministic",
        cacheable=False,
        run=_diff_run,
    )
)
