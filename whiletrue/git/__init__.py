"""Git workspaces (worktree per item) and commit/push/diff modules."""

from whiletrue.core import PluginApi, SimplePlugin
from whiletrue.git.modules import (
    git_commit_module,
    git_commit_push_module,
    git_diff_module,
)
from whiletrue.git.workspaces import DirCopy, GitWorktree, NoWorkspace


def _register(api: PluginApi) -> None:
    api.register_provider("workspace", "git-worktree", GitWorktree())
    api.register_provider("workspace", "dir-copy", DirCopy())
    api.register_provider("workspace", "none", NoWorkspace())
    api.register_action(git_commit_module)
    api.register_action(git_commit_push_module)
    api.register_action(git_diff_module)


git_plugin = SimplePlugin(name="whiletrue.git", register=_register)

__all__ = [
    "DirCopy",
    "GitWorktree",
    "NoWorkspace",
    "git_commit_module",
    "git_commit_push_module",
    "git_diff_module",
    "git_plugin",
]
