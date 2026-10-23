from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from whiletrue.core import RunItem
from whiletrue.core.source import SourceError


class JsonSource:
    """Items from a JSON file: a list of objects with an `id`, other fields, and `depends_on`."""

    async def fetch(self, config: dict[str, Any], cwd: str) -> list[RunItem]:
        path = config.get("path")
        if not isinstance(path, str) or not path:
            raise SourceError("`source.json` needs a `path`")
        try:
            data = json.loads((Path(cwd) / path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SourceError(f"Cannot read {path}: {error}") from error
        if not isinstance(data, list):
            raise SourceError(f"{path} must contain a JSON list of items")
        items: list[RunItem] = []
        for index, raw in enumerate(cast(list[Any], data)):
            if not isinstance(raw, dict) or "id" not in raw:
                raise SourceError(f"{path}: item {index} needs an `id`")
            fields = {str(k): v for k, v in cast(dict[Any, Any], raw).items()}
            depends = fields.pop("depends_on", [])
            items.append(
                RunItem(
                    str(fields.pop("id")), fields, [str(d) for d in cast(list[Any], depends or [])]
                )
            )
        return items
