from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, Field

from whiletrue.basics.process import run_command, tail
from whiletrue.core import (
    ActionModule,
    CommentRecord,
    CommentsContext,
    Emit,
    IncomingComment,
    Ports,
    PortSpec,
    RunItem,
    SimplePlugin,
    StepContext,
    WakeContext,
    WakeResult,
    define_action,
)
from whiletrue.core.module import PortValues
from whiletrue.github.client import (
    GhCliClient,
    GhComment,
    GitHubClient,
    GitHubError,
    repo_from_remote,
)
from whiletrue.github.specs import parse_spec_refs


async def _detect_repo(cwd: str) -> str:
    result = await run_command("git", ["remote", "get-url", "origin"], cwd=cwd)
    repo = repo_from_remote(result.stdout) if result.code == 0 else None
    if repo is None:
        raise GitHubError("Cannot tell which repository this is: set `repo: owner/name`")
    return repo


async def _repo(configured: str | None, cwd: str) -> str:
    return configured or await _detect_repo(cwd)


def _pr_ref(input: Any, item: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """The (repo, PR number) a step or a wake condition is about: the PR the previous step made."""
    source = cast(dict[str, Any], input) if isinstance(input, dict) else {}
    number = source.get("number") or (item or {}).get("pr")
    repo = source.get("repo") or (item or {}).get("repo")
    return (str(repo) if repo else None), (int(number) if number else None)


def _encode_thread(repo: str, number: int, comment: GhComment) -> str:
    return f"{repo}#{number}:{comment.id}:{comment.thread_root or ''}"


def _decode_thread(thread: str | None) -> tuple[str, int, GhComment] | None:
    if not thread or "#" not in thread:
        return None
    head, _, rest = thread.partition("#")
    number, _, tail_ = rest.partition(":")
    comment_id, _, root = tail_.rpartition(":")
    kind = "inline" if comment_id.startswith("inline:") else "issue"
    comment = GhComment(comment_id, kind, "", "", 0, thread_root=root or None)  # type: ignore[arg-type]
    return head, int(number), comment


class GithubIssuesSource:
    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def fetch(self, config: dict[str, Any], cwd: str) -> list[RunItem]:
        repo = await _repo(config.get("repo"), cwd)
        issues = await self._client.list_issues(
            repo,
            milestone=config.get("milestone"),
            state=str(config.get("state", "open")),
            labels=list(config.get("labels") or []),
        )
        numbers = {i.number for i in issues}
        use_blocked_by = config.get("depends_on", "blocked_by") == "blocked_by"
        return [
            RunItem(
                str(i.number),
                {
                    "number": i.number,
                    "title": i.title,
                    "body": i.body,
                    "labels": i.labels,
                    "url": i.url,
                    "repo": repo,
                    "spec_refs": parse_spec_refs(i.body),
                },
                [str(b) for b in i.blocked_by if b in numbers] if use_blocked_by else [],
            )
            for i in issues
        ]


class GithubPrComments:
    """Review threads and conversation comments on the PR the item opened."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def fetch(self, ctx: CommentsContext) -> list[IncomingComment]:
        repo, number = _pr_ref(ctx.input, ctx.item)
        if number is None:
            return []
        repo = await _repo(repo, ctx.cwd)
        found = await self._client.list_comments(repo, number)
        return [
            IncomingComment(
                external_id=c.id,
                author=c.author,
                body=c.body,
                created_at=c.created_at,
                thread=_encode_thread(repo, number, c),
                url=c.url,
                kind="reviewer" if c.author.endswith("[bot]") else "human",
            )
            for c in found
            if not c.body.lstrip().startswith("[whiletrue]")
        ]

    async def reply(self, comment: CommentRecord, body: str, ctx: CommentsContext) -> str | None:
        decoded = _decode_thread(comment.thread)
        if decoded is None:
            return None
        repo, number, target = decoded
        return await self._client.reply(repo, number, target, body)


class GithubReviewWake:
    """`github.pr_review`: `approved`, `changes_requested`, or omitted for either."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        repo, number = _pr_ref(ctx.input, ctx.item)
        if number is None:
            return None
        pr = await self._client.get_pr(await _repo(repo, ctx.cwd), number)
        wanted = {
            None: {"approved", "changes_requested"},
            "any": {"approved", "changes_requested"},
        }.get(arg, {str(arg)})
        if pr.review == "approved" and "approved" in wanted:
            return WakeResult("approved", "approved on GitHub", by="github")
        if pr.review == "changes_requested" and "changes_requested" in wanted:
            note = "\n\n".join(pr.review_notes) or "changes requested on GitHub"
            return WakeResult("rejected", note, by="github")
        return None


class GithubMergedWake:
    """`github.pr_merged`: someone merged the PR outside whiletrue."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def check(self, arg: Any, ctx: WakeContext) -> WakeResult | None:
        repo, number = _pr_ref(ctx.input, ctx.item)
        if number is None:
            return None
        pr = await self._client.get_pr(await _repo(repo, ctx.cwd), number)
        return (
            WakeResult("approved", "merged on GitHub", by="github")
            if pr.state == "merged"
            else None
        )


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


def _pr_value(pr: Any, repo: str) -> dict[str, Any]:
    return {
        "number": pr.number,
        "url": pr.url,
        "branch": pr.branch,
        "base": pr.base,
        "title": pr.title,
        "repo": repo,
    }


def github_plugin(client: GitHubClient | None = None) -> SimplePlugin:
    """Registers the GitHub source, modules, comments provider and wake conditions."""
    gh: GitHubClient = client if client is not None else GhCliClient()

    async def open_pr(input: PortValues, ctx: StepContext[OpenPrConfig]) -> Emit:
        cfg = ctx.config
        try:
            repo = await _repo(cfg.repo, ctx.cwd)
            if cfg.push:
                pushed = await run_command(
                    "git", ["push", "-u", "origin", cfg.head], cwd=ctx.cwd, signal=ctx.signal
                )
                if pushed.code != 0:
                    return Emit("fail", {"error": f"git push failed: {tail(pushed.stderr)}"})
            existing = await gh.find_pr(repo, cfg.head)
            if existing is not None:
                return Emit("ok", _pr_value(existing, repo))
            item: dict[str, Any] = ctx.scope.get("item") or {}
            title = cfg.title or str(item.get("title") or cfg.head)
            body = cfg.body + (f"\n\nCloses #{cfg.closes}" if cfg.closes else "")
            pr = await gh.create_pr(repo, head=cfg.head, base=cfg.base, title=title, body=body)
            return Emit("ok", _pr_value(pr, repo))
        except GitHubError as error:
            return Emit("fail", {"error": str(error)})

    async def sync_base(input: PortValues, ctx: StepContext[SyncBaseConfig]) -> Emit:
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

    async def merge(input: PortValues, ctx: StepContext[MergeConfig]) -> Emit:
        cfg = ctx.config
        in_repo, in_number = _pr_ref(input.get("in"), ctx.scope.get("item"))
        number = cfg.number or in_number
        if number is None:
            return Emit("fail", {"error": "github.merge needs a PR number (config or input)"})
        try:
            repo = await _repo(cfg.repo or in_repo, ctx.cwd)

            async def do() -> Emit:
                pr = await gh.get_pr(repo, number)
                if pr.state == "closed":
                    return Emit("fail", {"error": f"PR #{number} is closed"})
                if pr.state != "merged":
                    await gh.merge_pr(repo, number, cfg.method)
                    pr = await gh.get_pr(repo, number)
                return Emit("ok", {**_pr_value(pr, repo), "state": pr.state})

            if cfg.lock:
                async with ctx.locks.hold(cfg.lock):
                    return await do()
            return await do()
        except GitHubError as error:
            return Emit("fail", {"error": str(error)})

    pr_ports = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "PullRequest")])

    def register(api: Any) -> None:
        api.register_provider("source", "github_issues", GithubIssuesSource(gh))
        api.register_provider("comments", "github_pr", GithubPrComments(gh))
        api.register_provider("wake", "github.pr_review", GithubReviewWake(gh))
        api.register_provider("wake", "github.pr_merged", GithubMergedWake(gh))
        api.register_action(
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
                    run=open_pr,
                )
            )
        )
        api.register_action(
            define_action(
                ActionModule(
                    id="github.sync_base",
                    config=SyncBaseConfig,
                    ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                    effects="side-effect",
                    cacheable=False,
                    idempotency_key=lambda _i, ctx: f"github.sync_base:{ctx.step_key}",
                    run=sync_base,
                )
            )
        )
        api.register_action(
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
                        f"{ctx.config.number or _pr_ref(i.get('in'), None)[1]}"
                    ),
                    run=merge,
                )
            )
        )

    return SimplePlugin(name="whiletrue.github", register=register)
