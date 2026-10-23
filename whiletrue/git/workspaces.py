from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from whiletrue.basics.process import run_command, tail
from whiletrue.core import ItemState, Workspace, WorkspaceError


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "item"


async def _git(cwd: str, *args: str) -> str:
    result = await run_command("git", list(args), cwd=cwd)
    if result.code != 0:
        raise WorkspaceError(f"git {args[0]} failed: {tail(result.stderr or result.stdout)}")
    return result.stdout


class GitWorktree:
    """One git worktree per item, on its own branch, under `.whiletrue/worktrees/`.

    An existing worktree is reused, so a resumed run continues where the parked one left off.
    A finished (`done`) item's worktree is removed; its branch stays.
    """

    async def create(self, config: dict[str, Any], item: dict[str, Any], cwd: str) -> Workspace:
        item_id = str(item.get("id", "item"))
        branch = str(config.get("branch") or f"wt/{item_id}")
        base = str(config.get("base") or "HEAD")
        root = Path(cwd) / str(config.get("dir") or ".whiletrue/worktrees")
        path = root / _safe(item_id)
        if not (path / ".git").exists():
            root.mkdir(parents=True, exist_ok=True)
            await _git(cwd, "worktree", "prune")
            await _git(cwd, "worktree", "add", "-B", branch, str(path), base)

        async def release(state: ItemState) -> None:
            if state == "done":
                await run_command("git", ["worktree", "remove", "--force", str(path)], cwd=cwd)

        return Workspace(str(path), release)


class DirCopy:
    """A plain copy of the project directory per item (for projects that are not git repos)."""

    async def create(self, config: dict[str, Any], item: dict[str, Any], cwd: str) -> Workspace:
        root = Path(cwd) / str(config.get("dir") or ".whiletrue/copies")
        path = root / _safe(str(item.get("id", "item")))
        if not path.exists():
            ignore = (
                shutil.ignore_patterns(".whiletrue", ".git")
                if config.get("skip_git")
                else (shutil.ignore_patterns(".whiletrue"))
            )
            await asyncio.to_thread(shutil.copytree, cwd, path, ignore=ignore)

        async def release(state: ItemState) -> None:
            if state == "done":
                await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)

        return Workspace(str(path), release)


class NoWorkspace:
    """Items share the project directory."""

    async def create(self, config: dict[str, Any], item: dict[str, Any], cwd: str) -> Workspace:
        async def release(state: ItemState) -> None:
            return None

        return Workspace(cwd, release)
