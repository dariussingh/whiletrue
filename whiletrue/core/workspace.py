"""Workspaces give each item its own working directory (a git worktree, a copy, or the project)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from whiletrue.core.outcomes import ItemState
    from whiletrue.core.registry import Registry


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    #: The directory the item's steps run in.
    path: str
    #: Called once when the item reaches a final state for this run (`done` may clean up; a
    #: parked item keeps its workspace so a resume continues in it).
    release: Callable[[ItemState], Awaitable[None]]


class WorkspaceProvider(Protocol):
    async def create(self, config: dict[str, Any], item: dict[str, Any], cwd: str) -> Workspace:
        """Creates the workspace, or reuses it if it already exists (resume)."""
        ...


def workspace_spec(extra: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """`workspace: {name: {...config}}` -> (name, config). None means the project directory."""
    raw = extra.get("workspace")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw, {}
    if not isinstance(raw, dict) or len(raw) != 1:  # pyright: ignore[reportUnknownArgumentType]
        raise WorkspaceError("`workspace` must be a name or a mapping with one provider")
    ((name, config),) = raw.items()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    if config is not None and not isinstance(config, dict):
        raise WorkspaceError(f"`workspace.{name}` must be a mapping")
    return str(name), dict(config or {})  # pyright: ignore[reportUnknownArgumentType]


def resolve_workspace(registry: Registry, extra: dict[str, Any]) -> WorkspaceProvider | None:
    spec = workspace_spec(extra)
    if spec is None:
        return None
    provider: WorkspaceProvider | None = registry.get_provider("workspace", spec[0])
    if provider is None:
        raise WorkspaceError(f'Unknown workspace "{spec[0]}"')
    return provider
