# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified by the whiletrue authors: ported and refactored from
# https://github.com/google-research/harness2 (commit 1cfe254) into whiletrue.
# See third_party/harness2/PROVENANCE.md for the list of changes.
"""The evidence tree an improver reads. The only way evidence reaches a model.

    context/task.md                  the task as the agent received it
    context/rollouts/<label>.md      the full trajectory of a rollout, never clipped to fit
    context/deliverables/<label>.md  what the rollout produced (final answer and changed files)
    context/harness/<rel>            the harness being edited
    context/library/INDEX.md         earlier items in this domain with their harnesses
    context/pool/<label>/            a candidate harness and its rollout (compose and select)
    context/decisions/<label>.md     earlier composition notes
    MISSION.md                       what to do with all of it

Evidence is served as files and the prompt is a short pointer, so nothing is truncated to fit a
prompt. By default the evidence is blind: it never contains a verifier's verdict (a gate result),
and a tripwire refuses to build a tree that does.

Ported from Harness² `core/context.py`. Differences: rollouts arrive as plain `RolloutView`
values instead of benchmark adapters, the leak markers are whiletrue's gate names, and the
benchmark-specific paths (answer keys, rendered office files) are gone.
"""

from __future__ import annotations

import difflib
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.surfaces import SurfaceSpec
from whiletrue.rsi.harness2.tree import Tree

#: Strings that only a verifier's own output carries. Seeing one in evidence is a leak.
VERDICT_MARKERS: tuple[str, ...] = (
    "gate.shell",
    "gate.tests",
    "gate.diff_size",
    "gate.comments_answered",
)

_SCAN_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".log", ".py", ".sh", ".toml", ".diff", ".patch", ""}
)


class LeakError(ValueError):
    """Evidence would carry a verifier's verdict into a model's context."""


@dataclass(frozen=True)
class RolloutView:
    """What the method knows about one finished rollout. It never carries a score."""

    label: str
    ok: bool
    trajectory: str
    #: The final answer plus a description of the files the rollout changed.
    deliverable: str


@dataclass(frozen=True)
class PoolEntry:
    label: str
    tree: Tree
    rollout: RolloutView | None = None


@dataclass
class ContextTree:
    """A built evidence tree, plus what actually landed in it."""

    root: Path
    item_id: str
    rollouts: list[str]
    harness_files: int
    library: bool
    pool: list[str]
    decisions: int = 0
    failed: list[str] = field(default_factory=lambda: [])

    @property
    def layout(self) -> str:
        """The layout listing that goes in the mission; it only mentions what exists."""
        lines = ["context/task.md                  the task as the agent received it"]
        if self.decisions:
            lines.append("context/decisions/              earlier edits and composition decisions")
        if self.rollouts:
            names = ", ".join(f"{r}.md" for r in self.rollouts[:6])
            more = f" (+{len(self.rollouts) - 6} more)" if len(self.rollouts) > 6 else ""
            lines.append(f"context/rollouts/               FULL trajectories: {names}{more}")
            lines.append("context/deliverables/           what each rollout produced")
        if self.harness_files:
            lines.append(
                "context/harness/                the harness you are editing "
                f"({self.harness_files} files)"
            )
        if self.pool:
            lines.append(
                f"context/pool/                   {len(self.pool)} candidates, each with its "
                "harness, its diff and its rollout"
            )
        if self.library:
            lines.append(
                "context/library/                earlier items in this area: browse INDEX.md "
                "first, then open what looks relevant"
            )
        return "\n".join(lines)


def build(
    dest: Path,
    surface: SurfaceSpec,
    *,
    item_id: str,
    task: str,
    rollouts: Sequence[RolloutView] = (),
    harness: Tree | None = None,
    library_dir: Path | None = None,
    pool: Sequence[PoolEntry] = (),
    decisions: Mapping[str, str] | None = None,
    markers: Sequence[str] = VERDICT_MARKERS,
) -> ContextTree:
    """Write the evidence tree and return what landed, so the mission describes only that."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "task.md").write_text(task, encoding="utf-8")
    for label, notes in (decisions or {}).items():
        path = dest / "decisions" / f"{label}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(notes or "No decision notes returned.", encoding="utf-8")

    if rollouts:
        (dest / "rollouts").mkdir()
        (dest / "deliverables").mkdir()
    failed: list[str] = []
    for rollout in rollouts:
        (dest / "rollouts" / f"{rollout.label}.md").write_text(rollout.trajectory, encoding="utf-8")
        (dest / "deliverables" / f"{rollout.label}.md").write_text(
            rollout.deliverable or "(no deliverable produced)", encoding="utf-8"
        )
        if not rollout.ok:
            failed.append(rollout.label)

    harness_files = 0
    if harness is not None:
        harness_tree.write_tree(surface, harness, dest / "harness", stubs=True)
        harness_files = len(harness)
    labels = _write_pool(dest, surface, pool, harness or {})
    has_library = bool(library_dir and library_dir.is_dir() and any(library_dir.iterdir()))
    if has_library and library_dir is not None:
        shutil.copytree(library_dir, dest / "library", dirs_exist_ok=True)
    assert_no_verdicts(dest, markers)
    return ContextTree(
        root=dest,
        item_id=item_id,
        rollouts=[r.label for r in rollouts],
        harness_files=harness_files,
        library=has_library,
        pool=labels,
        decisions=len(decisions or {}),
        failed=failed,
    )


def _write_pool(
    dest: Path, surface: SurfaceSpec, pool: Sequence[PoolEntry], current: Tree
) -> list[str]:
    labels: list[str] = []
    for entry in pool:
        where = dest / "pool" / entry.label
        where.mkdir(parents=True, exist_ok=True)
        harness_tree.write_tree(surface, entry.tree, where / "harness", stubs=False)
        (where / "harness.diff").write_text(
            unified_diff(current, entry.tree, entry.label), encoding="utf-8"
        )
        if entry.rollout is not None:
            (where / "rollout.md").write_text(entry.rollout.trajectory, encoding="utf-8")
            (where / "produced.md").write_text(
                entry.rollout.deliverable or "(nothing produced)", encoding="utf-8"
            )
        labels.append(entry.label)
    return labels


def unified_diff(before: Tree, after: Tree, label: str) -> str:
    """A unified diff between two harness trees."""
    return "".join(
        line
        for rel in sorted(before.keys() | after.keys())
        for line in difflib.unified_diff(
            before.get(rel, "").splitlines(keepends=True),
            after.get(rel, "").splitlines(keepends=True),
            fromfile=f"current/{rel}",
            tofile=f"{label}/{rel}",
        )
    )


def assert_no_verdicts(root: Path, markers: Sequence[str] = VERDICT_MARKERS) -> None:
    """Refuse an evidence tree that contains a verifier's output, by file name and by content.

    A tripwire on the read boundary, not the defence itself: if it fires, find the read path that
    produced the match instead of widening the marker list.
    """
    if not markers or not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        for marker in markers:
            if marker in rel:
                raise LeakError(f"verdict would enter model context: {rel!r} matches {marker!r}")
        if path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for marker in markers:
            if marker in body:
                at = body.index(marker)
                raise LeakError(
                    f"verdict would enter model context: {rel!r} contains {marker!r} at offset "
                    f"{at} ({body[max(0, at - 60) : at + 120]!r})"
                )


def assert_tree_has_no_verdicts(
    tree: Mapping[str, str], markers: Sequence[str] = VERDICT_MARKERS
) -> None:
    """The same tripwire on an agent-authored harness tree, before it is rolled or stored.

    A tree carrying these strings could never be served as evidence to a later item.
    """
    for rel in sorted(tree):
        for marker in markers:
            if marker in rel:
                raise LeakError(f"harness file {rel!r} has verdict marker {marker!r} in its name")
        if Path(rel).suffix.lower() not in _SCAN_SUFFIXES:
            continue
        for marker in markers:
            if marker in tree[rel]:
                raise LeakError(f"harness file {rel!r} contains verdict marker {marker!r}")


def mission(tree: ContextTree, *, job: str) -> str:
    """The short pointer to the evidence on disk: the job, where the evidence is, how to use it."""
    return "\n".join(
        [
            job.strip(),
            "",
            "# YOUR EVIDENCE",
            "",
            "It is on disk, in `context/`. Read it; do not answer from this message alone.",
            "",
            tree.layout,
            "",
            "Every trajectory event is present: a trajectory was not clipped to fit this mission. "
            "An observation may carry an explicit truncation marker; that marker is ours, not "
            "behavior by the agent. Do not diagnose it as an agent failure.",
            "",
            "Read `task.md` and the current harness first. Inventory every available rollout or "
            "candidate and inspect each at least once before deciding. For a candidate, compare "
            "what changed from its editing baseline, what behavior changed in its rollout, what "
            "improved, and what regressed or remained unresolved. Then deep-read the evidence "
            "behind any edit. Cite the specific trajectory moment behind every change; a change "
            "you cannot trace to a moment is speculation.",
        ]
    )
