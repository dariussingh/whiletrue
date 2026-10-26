"""Stage results cached on disk, keyed by everything the stage could see."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast


def digest(root: Path, extra: str) -> str:
    """A digest of every file the session could read, plus its prompt."""
    h = hashlib.sha256(extra.encode())
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(root).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def load_cached(workdir: Path, key: str) -> dict[str, Any] | None:
    try:
        data: Any = json.loads((workdir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    saved = cast("dict[str, Any]", data)
    return saved if saved.get("key") == key else None


def store_cached(workdir: Path, key: str, **fields: Any) -> None:
    (workdir / "result.json").write_text(
        json.dumps({"key": key, **fields}, default=str, sort_keys=True), encoding="utf-8"
    )
