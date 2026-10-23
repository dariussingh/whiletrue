from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from whiletrue.core import RunItem
from whiletrue.core.source import SourceError

_TASK = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")
_META = re.compile(r"\s*\{(?P<meta>[^{}]*)\}\s*$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "task"


class MarkdownTasksSource:
    """Unchecked checklist lines of a markdown file are the items.

    `- [ ] Add the login page {#login after=schema,api}`: the optional `{...}` gives the item an
    id (default: a slug of the title) and the ids it waits for. Checked lines are done and skipped,
    so ticking a box in the file removes the item on the next resume (the source wins).
    """

    async def fetch(self, config: dict[str, Any], cwd: str) -> list[RunItem]:
        path = config.get("path")
        if not isinstance(path, str) or not path:
            raise SourceError("`source.markdown_tasks` needs a `path`")
        try:
            text = (Path(cwd) / path).read_text(encoding="utf-8")
        except OSError as error:
            raise SourceError(f"Cannot read {path}: {error}") from error
        items: list[RunItem] = []
        seen: set[str] = set()
        for number, line in enumerate(text.splitlines(), start=1):
            match = _TASK.match(line)
            if match is None or match["mark"] != " ":
                continue
            title = match["text"]
            item_id, depends = "", []
            meta = _META.search(title)
            if meta is not None:
                title = title[: meta.start()].strip()
                for token in meta["meta"].split():
                    if token.startswith("#"):
                        item_id = token[1:]
                    elif token.startswith("after="):
                        depends = [d for d in token[6:].split(",") if d]
            item_id = item_id or _slug(title)
            if item_id in seen:
                raise SourceError(f'{path}:{number}: duplicate task id "{item_id}"')
            seen.add(item_id)
            items.append(RunItem(item_id, {"title": title, "line": number}, depends))
        return items
