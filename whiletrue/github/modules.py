"""The GitHub action modules: open a PR, sync the base branch, merge."""

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
from whiletrue.github.client import GitHubClient, GitHubError
from whiletrue.github.refs import pr_ref, resolve_repo


class OpenPrConfig(BaseModel):
    repo: str | None = None
    #: The branch to open the PR from (templates apply, e.g. `wt/{{item.number}}`).
    head: str = Field(min_length=1)
    base: str = "main"
    title: str | None = None
    body: str = ""
    #: Issue number the PR closes; appended to the body as `Closes #N`.
    closes: int | str | None = None
    #: Push `head` to origin first.
    push: bool = True


class SyncBaseConfig(BaseModel):
    base: str = "main"
    strategy: str = Field(default="rebase", pattern="^rebase$")
    push: bool = True


class MergeConfig(BaseModel):
    repo: str | None = None
    number: int | None = None
    method: str = Field(default="squash", pattern="^(squash|merge|rebase)$")
    #: Hold this named lock while merging, so only one item merges at a time.
    lock: str | None = None


def pr_value(pr: Any, repo_name: str) -> dict[str, Any]:
    return {
        "number": pr.number,
        "url": pr.url,
        "branch": pr.branch,
        "base": pr.base,
        "title": pr.title,
        "repo": repo_name,
    }


class GithubModules:
    """The modules, bound to one GitHub client."""

    def __init__(self, client: GitHubClient) -> None:
        self._gh = client

    async def open_pr(self, input: PortValues, ctx: StepContext[OpenPrConfig]) -> Emit:
        cfg, gh = ctx.config, self._gh
        try:
            name = await resolve_repo(cfg.repo, ctx.cwd)
            if cfg.push:
                pushed = await run_command(
                    "git", ["push", "-u", "origin", cfg.head], cwd=ctx.cwd, signal=ctx.signal
                )
                if pushed.code != 0:
                    return Emit("fail", {"error": f"git push failed: {tail(pushed.stderr)}"})
            existing = await gh.find_pr(name, cfg.head)
            if existing is not None:
                return Emit("ok", pr_value(existing, name))
            item: dict[str, Any] = ctx.scope.get("item") or {}
            title = cfg.title or str(item.get("title") or cfg.head)
            body = cfg.body + (f"\n\nCloses #{cfg.closes}" if cfg.closes else "")
            pr = await gh.create_pr(name, head=cfg.head, base=cfg.base, title=title, body=body)
            return Emit("ok", pr_value(pr, name))
        except GitHubError as error:
            return Emit("fail", {"error": str(error)})

    async def sync_base(self, input: PortValues, ctx: StepContext[SyncBaseConfig]) -> Emit:
        cfg = ctx.config

        async def git(*args: str) -> Any:
            return await run_command("git", list(args), cwd=ctx.cwd, signal=ctx.signal)

        before = (await git("rev-parse", "HEAD")).stdout.strip()
        fetched = await git("fetch", "origin", cfg.base)
        if fetched.code != 0:
            return Emit("fail", {"error": f"git fetch failed: {tail(fetched.stderr)}"})
        rebased = await git("rebase", f"origin/{cfg.base}")
        if rebased.code != 0:
            conflicts = (await git("diff", "--name-only", "--diff-filter=U")).stdout.split()
            await git("rebase", "--abort")
            return Emit(
                "needs_human",
                {"reason": f"rebase onto {cfg.base} has conflicts", "files": conflicts},
            )
        after = (await git("rev-parse", "HEAD")).stdout.strip()
        if cfg.push and after != before:
            pushed = await git("push", "--force-with-lease", "origin", "HEAD")
            if pushed.code != 0:
                return Emit("fail", {"error": f"git push failed: {tail(pushed.stderr)}"})
        return Emit("ok", input.get("in"))

    async def _merge_pr(self, name: str, number: int, method: str) -> Emit:
        gh = self._gh
        pr = await gh.get_pr(name, number)
        if pr.state == "closed":
            return Emit("fail", {"error": f"PR #{number} is closed"})
        if pr.state != "merged":
            await gh.merge_pr(name, number, method)
            pr = await gh.get_pr(name, number)
        return Emit("ok", {**pr_value(pr, name), "state": pr.state})

    async def merge(self, input: PortValues, ctx: StepContext[MergeConfig]) -> Emit:
        cfg = ctx.config
        in_repo, in_number = pr_ref(input.get("in"), ctx.scope.get("item"))
        number = cfg.number or in_number
        if number is None:
            return Emit("fail", {"error": "github.merge needs a PR number (config or input)"})
        try:
            name = await resolve_repo(cfg.repo or in_repo, ctx.cwd)
            if cfg.lock:
                async with ctx.locks.hold(cfg.lock):
                    return await self._merge_pr(name, number, cfg.method)
            return await self._merge_pr(name, number, cfg.method)
        except GitHubError as error:
            return Emit("fail", {"error": str(error)})

    def actions(self) -> list[ActionModule[Any]]:
        """The three modules, ready to register."""
        pr_ports = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "PullRequest")])
        any_ports = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])
        return [
            define_action(
                ActionModule(
                    id="github.open_pr",
                    config=OpenPrConfig,
                    ports=pr_ports,
                    effects="side-effect",
                    cacheable=False,
                    journal=True,
                    idempotency_key=lambda _i, ctx: (
                        f"github.open_pr:{ctx.config.repo or 'origin'}:{ctx.config.head}"
                    ),
                    run=self.open_pr,
                )
            ),
            define_action(
                ActionModule(
                    id="github.sync_base",
                    config=SyncBaseConfig,
                    ports=any_ports,
                    effects="side-effect",
                    cacheable=False,
                    idempotency_key=lambda _i, ctx: f"github.sync_base:{ctx.step_key}",
                    run=self.sync_base,
                )
            ),
            define_action(
                ActionModule(
                    id="github.merge",
                    config=MergeConfig,
                    ports=pr_ports,
                    effects="side-effect",
                    cacheable=False,
                    journal=True,
                    idempotency_key=lambda i, ctx: (
                        f"github.merge:{ctx.config.repo or 'origin'}:"
                        f"{ctx.config.number or pr_ref(i.get('in'), None)[1]}"
                    ),
                    run=self.merge,
                )
            ),
        ]
