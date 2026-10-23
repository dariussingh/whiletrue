"""GitHub integration: issues as items, PRs, review comments, merge, and wake conditions."""

from whiletrue.github.client import (
    GhCliClient,
    GhComment,
    GhIssue,
    GhPullRequest,
    GitHubClient,
    GitHubError,
    repo_from_remote,
)
from whiletrue.github.memory import InMemoryGitHub
from whiletrue.github.plugin import github_plugin
from whiletrue.github.specs import blocked_by_in_body, parse_spec_refs

__all__ = [
    "GhCliClient",
    "GhComment",
    "GhIssue",
    "GhPullRequest",
    "GitHubClient",
    "GitHubError",
    "InMemoryGitHub",
    "blocked_by_in_body",
    "github_plugin",
    "parse_spec_refs",
    "repo_from_remote",
]
