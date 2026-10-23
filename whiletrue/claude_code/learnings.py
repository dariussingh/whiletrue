from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class LearningsStore:
    """Append-only JSONL of notes agents record with `whiletrue_learn`, each with provenance."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, note: str, **provenance: Any) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        row = {"note": note, "at": int(time.time() * 1000), **provenance}
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def recent(self, limit: int) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)  # pyright: ignore[reportUnknownArgumentType]
        return rows[-limit:]
