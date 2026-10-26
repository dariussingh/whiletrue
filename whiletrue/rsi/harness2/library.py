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
"""The per-domain experience library: what earlier items in this domain produced.

    <root>/<domain>/INDEX.md
    <root>/<domain>/harnesses/<item>/harness/<rel>   the full adopted harness
    <root>/<domain>/harnesses/<item>/EXPERIENCE.md   why it was picked, plus a transferable lesson

Retrieval is by construction: a domain's directory only ever holds same-domain entries. The
whole tree is carried, never a summary. An entry is stored only when its experience has a cue, a
lesson and evidence.

Ported from Harness² `core/library.py`. Differences: typed `Experience`, an explicit root
instead of a per-benchmark layout, and the verdict tripwire from our `context` module.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.surfaces import SurfaceSpec
from whiletrue.rsi.harness2.tree import Tree


@dataclass(frozen=True)
class Experience:
    """A transferable lesson. `cue`, `lesson` and `evidence` are required for it to be kept."""

    cue: str = ""
    lesson: str = ""
    evidence: str = ""
    edits: str = ""
    confidence: str = ""

    @property
    def complete(self) -> bool:
        return all(text.strip() for text in (self.cue, self.lesson, self.evidence))


def slug(item_id: str) -> str:
    return item_id.replace("/", "__")


def domain_dir(domain: str) -> str:
    """One directory name for a domain, never a path: separators collapse, nothing else does."""
    return re.sub(r"\s*[/\\]\s*", "__", domain).strip() or "unknown"


@dataclass
class Library:
    """One domain's library: read before each item, appended after."""

    root: Path
    domain: str
    surface: SurfaceSpec

    @property
    def dir(self) -> Path:
        return self.root / domain_dir(self.domain)

    @property
    def index(self) -> Path:
        return self.dir / "INDEX.md"

    def entry_dir(self, item_id: str) -> Path:
        return self.dir / "harnesses" / slug(item_id)

    def exists(self) -> bool:
        return self.index.is_file()

    def context_dir(self) -> Path | None:
        """The directory handed to an improver, or None when this domain is empty."""
        has_entries = self.exists() and any(self.dir.rglob("EXPERIENCE.md"))
        return self.dir if has_entries else None

    def entries(self) -> list[str]:
        folder = self.dir / "harnesses"
        return sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []

    def tree_of(self, item_id: str) -> Tree:
        return harness_tree.read_tree(self.surface, self.entry_dir(item_id) / "harness")

    def forget(self, item_ids: Sequence[str]) -> None:
        """Drop the entries for these items and rebuild INDEX.md; unknown items are ignored."""
        removed = False
        for item_id in item_ids:
            folder = self.entry_dir(item_id)
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
                removed = True
        if removed:
            self.rebuild_index()

    def add(
        self,
        item_id: str,
        harness: Tree,
        experience: Experience,
        *,
        why: str = "",
        label: str = "",
        run_id: str = "",
    ) -> Path | None:
        """Store one adopted harness and why it was adopted; rebuild INDEX.md from disk.

        Returns None (and stores nothing) when the experience is incomplete. The harness and the
        text are checked for verdicts before anything is written.
        """
        if not experience.complete:
            return None
        context.assert_tree_has_no_verdicts(harness)
        context.assert_tree_has_no_verdicts({"EXPERIENCE.md": repr(experience) + why})
        folder = self.entry_dir(item_id)
        shutil.rmtree(folder / "harness", ignore_errors=True)
        (folder / "harness").mkdir(parents=True, exist_ok=True)
        harness_tree.write_tree(self.surface, harness, folder / "harness", stubs=False)
        lines = [
            f"# {item_id}",
            "",
            f"- adopted: `{label}`" + (f" (run `{run_id}`)" if run_id else ""),
            f"- components: {_inventory(self.surface, harness)}",
            "",
        ]
        if why.strip():
            lines += ["## Why this was chosen", why.strip(), ""]
        if experience.edits.strip():
            lines += ["## What each edit did", experience.edits.strip(), ""]
        lines += ["## Transferable lesson"]
        for name in ("cue", "lesson", "evidence", "confidence"):
            value = getattr(experience, name)
            if value:
                lines.append(f"- **{name}**: {value.strip()}")
        lines.append("")
        (folder / "EXPERIENCE.md").write_text("\n".join(lines), encoding="utf-8")
        self.rebuild_index()
        return folder

    def rebuild_index(self) -> Path:
        """Regenerate INDEX.md from what is on disk."""
        rows = [
            f"# Library: {self.domain}",
            "",
            "Each entry is an earlier item in THIS domain whose harness was chosen, stored with "
            "its FULL component tree and why it was picked. Browse this index, then open the "
            "entries that look relevant to the matter in front of you.",
            "",
        ]
        names = self.entries()
        if not names:
            rows.append("_(empty: this is the first item in this domain)_")
        for name in names:
            entry = self.dir / "harnesses" / name
            cue = _field(entry / "EXPERIENCE.md", "cue")
            tree = harness_tree.read_tree(self.surface, entry / "harness")
            rows += [
                f"## `harnesses/{name}/`",
                f"- harness: `harnesses/{name}/harness/`: {_inventory(self.surface, tree)}",
                f"- why:     `harnesses/{name}/EXPERIENCE.md`",
            ]
            if cue:
                rows.append(f"- cue:     {cue}")
            rows.append("")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index.write_text("\n".join(rows), encoding="utf-8")
        return self.index


def _inventory(surface: SurfaceSpec, tree: Tree) -> str:
    """A one-line census of the components actually in this harness."""
    skills = len(harness_tree.skill_names(surface, tree))
    subs = len(harness_tree.subagent_names(surface, tree))
    scripts = sum(1 for rel in tree if surface.script_pattern.fullmatch(rel))
    counts = {"skills": skills, "subagents": subs, "scripts": scripts}
    bits = [f"{n} {name}" for name, n in counts.items() if n] or ["always-on edits only"]
    chars = len(harness_tree.always_on_text(surface, tree))
    return f"{', '.join(bits)}; {chars:,} always-on chars"


def _field(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"- **{name}**:"):
            return line.split(":", 1)[1].strip()
    return ""
