"""`whiletrue.lock` (plugin version pins) and content hashes of the files a run depends on."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from whiletrue.core.config import PipelineConfig, PipelineNode

LOCK_FILE = "whiletrue.lock"
LOCK_VERSION = 1


class LockError(Exception):
    pass


def write_lock(path: Path, plugins: dict[str, str]) -> None:
    body = {"version": LOCK_VERSION, "plugins": dict(sorted(plugins.items()))}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def read_lock(path: Path) -> dict[str, str] | None:
    """The pinned plugin versions, or None when there is no lock file."""
    if not path.is_file():
        return None
    try:
        body: Any = json.loads(path.read_text(encoding="utf-8"))
        plugins = body["plugins"]
        if body["version"] != LOCK_VERSION or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in plugins.items()
        ):
            raise ValueError("unsupported format")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise LockError(f"Cannot read {path.name}: {error}") from error
    return dict(plugins)


def check_lock(pinned: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Problems between the lock and the plugins in use; empty means they agree."""
    problems: list[str] = []
    for name, version in sorted(actual.items()):
        if name not in pinned:
            problems.append(f'Plugin "{name}" ({version}) is not in {LOCK_FILE}')
        elif pinned[name] != version:
            problems.append(f'Plugin "{name}" is {version} but {LOCK_FILE} pins {pinned[name]}')
    problems += [
        f'{LOCK_FILE} pins "{name}" which is no longer used'
        for name in sorted(set(pinned) - set(actual))
    ]
    return problems


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _uses_paths(node: PipelineNode) -> list[str]:
    found: list[str] = []
    uses = node.config.get("uses")
    if isinstance(uses, str) and not uses.startswith("recipe:"):
        found.append(uses)
    for child in node.children:
        found += _uses_paths(child)
    return found


def collect_file_hashes(config: PipelineConfig, cwd: str) -> dict[str, str]:
    """Hashes of the pipeline file, expanded composites and any file a step `uses:` (prompts)."""
    base = Path(cwd)
    candidates = [Path(config.file), *map(Path, config.sources)]
    candidates += [base / p for p in _uses_paths(config.root)]
    hashes: dict[str, str] = {}
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else base / candidate
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            key = str(resolved.relative_to(base.resolve()))
        except ValueError:
            key = str(resolved)
        hashes[key] = sha256_file(resolved)
    return dict(sorted(hashes.items()))
