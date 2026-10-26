from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from whiletrue.github.client import GhComment, GhIssue, GhPullRequest
from whiletrue.github.memory import InMemoryGitHub


class FileBackedGitHub(InMemoryGitHub):
    """An in-memory GitHub whose state lives in a JSON file.

    For dry runs of a real pipeline without a repository: issues, PRs, comments, replies and
    merges survive across processes, so crash/resume and review loops can be tried for real.
    Edit the file (or call the helpers) to play the reviewer.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        self.issues = [GhIssue(**i) for i in data.get("issues", [])]
        self.prs = {int(k): GhPullRequest(**v) for k, v in data.get("prs", {}).items()}
        self.comments = {
            int(k): [GhComment(**c) for c in v] for k, v in data.get("comments", {}).items()
        }
        self.replies = [tuple(r) for r in data.get("replies", [])]  # type: ignore[misc]
        self.merged = [tuple(m) for m in data.get("merged", [])]  # type: ignore[misc]
        self.created = list(data.get("created", []))
        self._next_pr = int(data.get("next_pr", 1))
        self._next_comment = int(data.get("next_comment", 1000))

    def save(self) -> None:
        data = {
            "issues": [asdict(i) for i in self.issues],
            "prs": {str(k): asdict(v) for k, v in self.prs.items()},
            "comments": {str(k): [asdict(c) for c in v] for k, v in self.comments.items()},
            "replies": [list(r) for r in self.replies],
            "merged": [list(m) for m in self.merged],
            "created": self.created,
            "next_pr": self._next_pr,
            "next_comment": self._next_comment,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Every call re-reads the file first (another process may have changed it) and saves after.

    async def list_issues(self, *args: Any, **kwargs: Any) -> list[GhIssue]:
        self._load()
        return await super().list_issues(*args, **kwargs)

    async def find_pr(self, *args: Any, **kwargs: Any) -> GhPullRequest | None:
        self._load()
        return await super().find_pr(*args, **kwargs)

    async def create_pr(self, *args: Any, **kwargs: Any) -> GhPullRequest:
        self._load()
        pr = await super().create_pr(*args, **kwargs)
        self.save()
        return pr

    async def get_pr(self, *args: Any, **kwargs: Any) -> GhPullRequest:
        self._load()
        return await super().get_pr(*args, **kwargs)

    async def list_comments(self, *args: Any, **kwargs: Any) -> list[GhComment]:
        self._load()
        return await super().list_comments(*args, **kwargs)

    async def reply(self, *args: Any, **kwargs: Any) -> str:
        self._load()
        ref = await super().reply(*args, **kwargs)
        self.save()
        return ref

    async def merge_pr(self, *args: Any, **kwargs: Any) -> None:
        self._load()
        await super().merge_pr(*args, **kwargs)
        self.save()
