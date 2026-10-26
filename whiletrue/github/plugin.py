"""The GitHub plugin: registers the source, comments provider, wake conditions and modules."""

from __future__ import annotations

from typing import Any

from whiletrue.core import SimplePlugin
from whiletrue.github.client import GhCliClient, GitHubClient
from whiletrue.github.modules import GithubModules
from whiletrue.github.providers import (
    GithubIssuesSource,
    GithubMergedWake,
    GithubPrComments,
    GithubReviewWake,
)


def github_plugin(client: GitHubClient | None = None) -> SimplePlugin:
    """Registers the GitHub source, modules, comments provider and wake conditions."""
    gh: GitHubClient = client if client is not None else GhCliClient()

    def register(api: Any) -> None:
        api.register_provider("source", "github_issues", GithubIssuesSource(gh))
        api.register_provider("comments", "github_pr", GithubPrComments(gh))
        api.register_provider("wake", "github.pr_review", GithubReviewWake(gh))
        api.register_provider("wake", "github.pr_merged", GithubMergedWake(gh))
        for module in GithubModules(gh).actions():
            api.register_action(module)

    return SimplePlugin(name="whiletrue.github", register=register)
