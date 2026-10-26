"""Isolated copies of an item's workspace, and the change a rollout made in one.

A rollout runs in a snapshot (a copy of the item's directory, taken from its current state,
uncommitted changes included) so candidates never touch each other or the item's own files.
After the rollout, `changes` compares the snapshot with a manifest taken when it was made; that
change set is both the rollout's deliverable (what the selector reads) and what is applied back to
the item's directory when the rollout is chosen.

Plain file copies are used instead of git worktrees so this works outside git and picks up
untracked files. `.git` is not copied by default: a rollout that needs history should say so
(`rsi.copy_git`).
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

#: Never copied: version control, whiletrue's own state, caches and dependency trees.
DEFAULT_EXCLUDE = (
    ".git",
    ".whiletrue",
    "__pycache__",
    "*.pyc",
    "node_modules",
    ".venv",
    ".devenv",
    ".direnv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)
MAX_DIFF_CHARS = 40_000


class SnapshotError(RuntimeError):
    """A snapshot could not be made or applied."""


def _excluded(rel: str, patterns: Iterable[str]) -> bool:
    parts = rel.split("/")
    return any(
        fnmatch.fnmatch(rel, pattern) or any(fnmatch.fnmatch(part, pattern) for part in parts)
        for pattern in patterns
    )


def _join(base: str, name: str) -> str:
    return name if base == "." else f"{base}/{name}"


def _files(root: Path, exclude: Iterable[str]) -> list[str]:
    patterns = tuple(exclude)
    found: list[str] = []
    for current, dirs, names in os.walk(root):
        base = Path(current).relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if not _excluded(_join(base, d), patterns))
        for name in sorted(names):
            rel = _join(base, name)
            if not _excluded(rel, patterns):
                found.append(rel)
    return found


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Snapshot:
    """A copy of a directory, with the manifest of what it held when it was taken."""

    source: Path
    path: Path
    exclude: tuple[str, ...]
    manifest: dict[str, str] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class Changes:
    """What a rollout changed: relative path to new content (`None` means deleted)."""

    files: dict[str, bytes | None]
    #: A unified diff of the text changes, for people and the selector to read.
    diff: str

    @property
    def paths(self) -> list[str]:
        return sorted(self.files)

    def __bool__(self) -> bool:
        return bool(self.files)


def take(
    source: Path, dest: Path, *, exclude: Iterable[str] = DEFAULT_EXCLUDE, copy_git: bool = False
) -> Snapshot:
    """Copy `source` to `dest` (replacing it) and record the manifest."""
    patterns = tuple(p for p in exclude if not (copy_git and p == ".git"))
    if not source.is_dir():
        raise SnapshotError(f"cannot snapshot {source}: not a directory")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    manifest: dict[str, str] = {}
    for rel in _files(source, patterns):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, target)
        manifest[rel] = _digest(target)
    return Snapshot(source=source, path=dest, exclude=patterns, manifest=manifest)


def refresh(snapshot: Snapshot) -> None:
    """Re-record the manifest from what is in the snapshot now (after writing a harness into it)."""
    snapshot.manifest = {
        rel: _digest(snapshot.path / rel) for rel in _files(snapshot.path, snapshot.exclude)
    }


def changes(snapshot: Snapshot, *, skip: Callable[[str], bool] | None = None) -> Changes:
    """The files that differ from the snapshot's manifest, with a readable diff.

    `skip` leaves out paths that are not part of the deliverable (the harness itself).
    """
    now = {
        rel: snapshot.path / rel
        for rel in _files(snapshot.path, snapshot.exclude)
        if skip is None or not skip(rel)
    }
    files: dict[str, bytes | None] = {}
    diff: list[str] = []
    for rel, path in sorted(now.items()):
        digest = _digest(path)
        if snapshot.manifest.get(rel) == digest:
            continue
        files[rel] = path.read_bytes()
        diff.append(_diff(snapshot, rel, path))
    for rel in sorted(r for r in set(snapshot.manifest) - set(now) if skip is None or not skip(r)):
        files[rel] = None
        diff.append(_diff(snapshot, rel, None))
    text = "".join(diff)
    if len(text) > MAX_DIFF_CHARS:
        text = (
            text[:MAX_DIFF_CHARS]
            + f"\n[diff truncated: {len(text) - MAX_DIFF_CHARS:,} characters omitted]\n"
        )
    return Changes(files=files, diff=text)


def _read_text(path: Path | None) -> list[str] | None:
    if path is None or not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def _diff(snapshot: Snapshot, rel: str, new: Path | None) -> str:
    before = _read_text(snapshot.source / rel) if rel in snapshot.manifest else []
    after = _read_text(new)
    if before is None or after is None:
        return f"Binary file {rel} changed\n"
    return (
        "".join(difflib.unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        or f"{rel}: mode or empty-file change\n"
    )


def apply(change_set: Changes, directory: Path) -> list[str]:
    """Write a change set into `directory`; return the paths touched."""
    root = directory.resolve()
    for rel in change_set.files:
        target = (directory / rel).resolve()
        if root != target and root not in target.parents:
            raise SnapshotError(f"change escapes the directory: {rel!r}")
    for rel, content in change_set.files.items():
        target = directory / rel
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return change_set.paths


def describe(change_set: Changes, final: str) -> str:
    """The deliverable text a selector reads: the final message and the change set."""
    parts = [f"## Final message\n{final.strip() or '(none)'}\n"]
    if change_set:
        parts.append(
            f"## Files changed ({len(change_set.files)})\n"
            + "\n".join(f"- {p}" for p in change_set.paths)
            + "\n"
        )
        parts.append(f"## Diff\n```diff\n{change_set.diff}\n```\n")
    else:
        parts.append("## Files changed\n(none)\n")
    return "\n".join(parts)


def save(change_set: Changes, directory: Path) -> None:
    """Persist a change set so a finished rollout can be reused after a restart."""
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "files").mkdir(parents=True)
    deleted: list[str] = []
    for rel, content in change_set.files.items():
        if content is None:
            deleted.append(rel)
            continue
        target = directory / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (directory / "diff.patch").write_text(change_set.diff, encoding="utf-8")
    (directory / "deleted.txt").write_text("\n".join(deleted), encoding="utf-8")


def load(directory: Path) -> Changes:
    """The change set written by `save`."""
    files: dict[str, bytes | None] = {}
    root = directory / "files"
    for rel in _files(root, ()):
        files[rel] = (root / rel).read_bytes()
    deleted = (directory / "deleted.txt").read_text(encoding="utf-8")
    for rel in filter(None, deleted.split("\n")):
        files[rel] = None
    return Changes(files=files, diff=(directory / "diff.patch").read_text(encoding="utf-8"))
