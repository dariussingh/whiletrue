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
"""The editable component tree: read, validate, write and diff a harness.

A tree is a mapping of relative path to file content, restricted to the paths a `SurfaceSpec`
allows. The edit contract is omitted-is-inherited: a proposal changes only the files it writes.

Ported from Harness² `core/tree.py`. Differences: typed results (`Census`, `Diff`) instead of
dicts, `restricted_changes` to find edits that need approval, and all path rules come from the
`SurfaceSpec` passed in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from whiletrue.rsi.harness2.surfaces import SurfaceSpec

Tree = dict[str, str]

#: Scripts the runtime itself provides; a skill may invoke them without shipping them.
RESERVED_SCRIPTS = frozenset({"read_doc.py", "pack.py", "unpack.py"})


class TreeError(ValueError):
    """A harness tree broke a rule. The message lists every problem found."""


@dataclass(frozen=True)
class Census:
    """What a validated tree contains."""

    always_on_chars: int
    n_files: int
    skills: int
    sub_agents: int
    scripts: int
    plugins: int = 0
    hooks: int = 0


@dataclass(frozen=True)
class Diff:
    added: list[str] = field(default_factory=lambda: [])
    removed: list[str] = field(default_factory=lambda: [])
    modified: list[str] = field(default_factory=lambda: [])
    always_on_char_delta: int = 0

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    @property
    def paths(self) -> list[str]:
        return sorted({*self.added, *self.removed, *self.modified})


def normalize_rel(rel: str) -> str:
    """Normalise a relative path, rejecting escapes before it is joined to a directory."""
    path = str(rel).strip().replace("\\", "/")
    if path.startswith("/") or ".." in path.split("/"):
        raise TreeError(f"path escapes the harness: {rel!r}")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def is_allowed(surface: SurfaceSpec, rel: str) -> bool:
    """Whether this path is on the editable surface. A predicate: it never raises."""
    try:
        path = normalize_rel(rel)
    except TreeError:
        return False
    if path in surface.always_on or (surface.hooks_file is not None and path == surface.hooks_file):
        return True
    patterns = [surface.skill_pattern, surface.skill_script_pattern, surface.script_pattern]
    patterns += [p for p in (surface.subagent_pattern, surface.plugin_pattern) if p is not None]
    return any(pattern.fullmatch(path) for pattern in patterns)


def _names(pattern: re.Pattern[str] | None, tree: Tree) -> list[str]:
    if pattern is None:
        return []
    return sorted({m.group(1) for rel in tree if (m := pattern.fullmatch(rel))})


def skill_names(surface: SurfaceSpec, tree: Tree) -> list[str]:
    return _names(surface.skill_pattern, tree)


def subagent_names(surface: SurfaceSpec, tree: Tree) -> list[str]:
    return _names(surface.subagent_pattern, tree)


def _stub_paths(surface: SurfaceSpec) -> frozenset[str]:
    return frozenset(rel for rel, _ in surface.stubs)


def read_tree(
    surface: SurfaceSpec, directory: Path | str, *, rejected: list[str] | None = None
) -> Tree:
    """Relative path to content for every file on the editable surface, stubs excluded.

    Pass `rejected` to collect paths that exist on disk but are off the surface.
    """
    root = Path(directory)
    out: Tree = {}
    if not root.is_dir():
        return out
    stubs = _stub_paths(surface)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in stubs:
            continue
        if not is_allowed(surface, rel):
            if rejected is not None:
                rejected.append(rel)
            continue
        out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def is_tree(surface: SurfaceSpec, directory: Path | str) -> bool:
    root = Path(directory)
    return root.is_dir() and any((root / name).is_file() for name in surface.always_on)


def write_tree(
    surface: SurfaceSpec,
    tree: Tree,
    dest: Path | str,
    *,
    stubs: bool = True,
    create_always_on: bool = False,
) -> Path:
    """Materialise an exact editable tree under `dest`.

    Editable files already there are replaced by the tree (so a deleted component is removed);
    files off the surface are left alone. With `create_always_on`, missing always-on files are
    created empty (some frameworks fail to start without them).
    """
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    stub_set = _stub_paths(surface)
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_allowed(surface, rel) or rel in stub_set:
            path.unlink()
    for name in surface.always_on if create_always_on else ():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name not in tree and not path.exists():
            path.write_text("", encoding="utf-8")
    for rel, content in tree.items():
        path = root / normalize_rel(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if stubs:
        for rel, body in surface.stubs:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(body, encoding="utf-8")
    return root


def always_on_text(surface: SurfaceSpec, tree: Tree) -> str:
    return "\n\n".join(tree.get(name, "") for name in surface.always_on)


def autotrigger(surface: SurfaceSpec, tree: Tree, *, always_on_max: int | None = None) -> list[str]:
    """Give every skill a trigger line in always-on text; return the names repaired.

    A line that would push the body over the always-on cap is skipped instead.
    """
    cap = surface.always_on_max if always_on_max is None else always_on_max
    body = always_on_text(surface, tree)
    fixed: list[str] = []
    for name in skill_names(surface, tree):
        if name in body:
            continue
        head = tree.get(f"{surface.skill_dir}/{name}/SKILL.md", "")[:600]
        match = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
        desc = match.group(1).strip().rstrip(".") if match else f"the {name} workflow applies"
        line = surface.trigger_line.format(name=name, desc=desc)
        current = tree.get(surface.autotrigger_file, "")
        if len(always_on_text(surface, tree)) + len(line) + 2 > cap:
            continue
        tree[surface.autotrigger_file] = (
            (current.rstrip() + "\n" + line) if current.strip() else line
        )
        body = always_on_text(surface, tree)
        fixed.append(name)
    return fixed


def check_hooks(surface: SurfaceSpec, source: str, errors: list[str]) -> None:
    """Validate the hooks file against the schema the framework dispatches.

    A malformed hooks file never fires instead of erroring, so the shape is enforced here.
    """
    name = surface.hooks_file
    try:
        doc: Any = json.loads(source)
    except json.JSONDecodeError as error:
        errors.append(f"{name} is not valid JSON: {error}")
        return
    hooks: Any = cast("dict[str, Any]", doc).get("hooks") if isinstance(doc, dict) else None
    if not isinstance(hooks, dict) or not hooks:
        errors.append(f'{name} needs a top-level {{"hooks": {{"<Event>": [...]}}}} object')
        return
    for event, entries in hooks.items():  # pyright: ignore[reportUnknownVariableType]
        _check_hook_event(surface, str(event), entries, errors)  # pyright: ignore[reportUnknownArgumentType]


def _check_hook_event(surface: SurfaceSpec, event: str, entries: Any, errors: list[str]) -> None:
    name = surface.hooks_file
    if event not in surface.hook_events:
        known = ", ".join(sorted(surface.hook_events))
        errors.append(f"{name}: unknown event {event!r}; the framework dispatches {known}")
        return
    if not isinstance(entries, list):
        errors.append(f"{name}: {event} must map to a LIST of matcher entries")
        return
    for i, entry in enumerate(entries):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):  # pyright: ignore[reportUnknownMemberType]
            errors.append(f"{name}: {event}[{i}] needs a `hooks` list")
            continue
        for hook in entry["hooks"]:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(hook, dict) or hook.get("type") != "command":  # pyright: ignore[reportUnknownMemberType]
                errors.append(f'{name}: {event}[{i}] hook type must be "command"')
            elif not str(hook.get("command", "")).strip():  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                errors.append(f"{name}: {event}[{i}] has an empty `command`")


def _py_compiles(rel: str, source: str, errors: list[str]) -> None:
    try:
        compile(source, rel, "exec")
    except (SyntaxError, ValueError) as error:
        detail = (
            f"{type(error).__name__}: {error.msg} (line {error.lineno})"
            if isinstance(error, SyntaxError)
            else f"{type(error).__name__}: {error}"
        )
        errors.append(f"{rel} does not compile: {detail[:160]}")


def _check_skills(surface: SurfaceSpec, tree: Tree, body: str, errors: list[str]) -> list[str]:
    """Append every skill-frontmatter and untriggered-skill problem; return the skill names."""
    skills = skill_names(surface, tree)
    for name in skills:
        path = f"{surface.skill_dir}/{name}/SKILL.md"
        head = tree[path][:600]
        if not re.search(r"^description:\s*\S", head, re.MULTILINE):
            errors.append(
                f"{path} needs a `description:` in its frontmatter, or the skill is dropped "
                "SILENTLY at load"
            )
        declared = re.search(r"^name:\s*(\S+)\s*$", head, re.MULTILINE)
        if surface.skill_name_rule == "must_match":
            if not (declared and declared.group(1) == name):
                errors.append(
                    f"{path} needs frontmatter `name: {name}` matching its directory (a mismatch "
                    "is dropped SILENTLY at load)"
                )
        elif declared and declared.group(1) != name:
            errors.append(
                f"{path} frontmatter `name: {declared.group(1)}` OVERRIDES the directory name at "
                f"load, so every trigger line naming `{name}` stops matching; drop the `name:` "
                f"line or make it `name: {name}`"
            )
        if name not in body:
            errors.append(
                f"skill '{name}' is never named in the always-on text, so the agent will not "
                "know to invoke it. " + surface.trigger_hint.format(name=name)
            )
    return skills


def _check_subagents(
    surface: SurfaceSpec, tree: Tree, allow_subagents: bool, errors: list[str]
) -> list[str]:
    subs = subagent_names(surface, tree)
    if subs and not allow_subagents:
        errors.append(f"subagents are not available on this deployment: {subs}")
    pattern = surface.subagent_pattern
    for rel in sorted(tree):
        if pattern is None or not pattern.fullmatch(rel):
            continue
        head = tree[rel][:600]
        if surface.subagent_requires_mode and not re.search(
            r"^mode:\s*subagent\s*$", head, re.MULTILINE
        ):
            errors.append(f"{rel} needs frontmatter `mode: subagent`")
        if not re.search(r"^description:\s*\S", head, re.MULTILINE):
            errors.append(f"{rel} needs a `description:`")
    return subs


def _check_script_refs(
    surface: SurfaceSpec, tree: Tree, reserved: frozenset[str], errors: list[str]
) -> None:
    """Append a problem for every script a skill or always-on file invokes but does not ship."""
    have = {
        rel
        for rel in tree
        if surface.script_pattern.fullmatch(rel) or surface.skill_script_pattern.fullmatch(rel)
    }
    for rel, source in tree.items():
        if not (surface.skill_pattern.fullmatch(rel) or rel in surface.always_on):
            continue
        for ref in surface.script_ref_pattern.findall(source):
            if ref.rsplit("/", 1)[-1] in reserved:
                continue
            if ref not in have:
                errors.append(f"{rel} invokes `{ref}` but that file is not in the harness")


def validate(
    surface: SurfaceSpec,
    tree: Tree,
    *,
    allow_subagents: bool = True,
    always_on_max: int | None = None,
    reserved_scripts: frozenset[str] = RESERVED_SCRIPTS,
) -> Census:
    """Raise `TreeError` listing every problem; return a census of the validated tree."""
    errors: list[str] = []
    cap = surface.always_on_max if always_on_max is None else always_on_max
    body = always_on_text(surface, tree)

    if len(body) > cap:
        errors.append(
            f"always-on content is {len(body)} chars, over the {cap} cap; "
            f"{surface.always_on_cap_reason}"
        )
    if surface.reject_empty_always_on and not body.strip():
        errors.append(
            f"{surface.always_on[0]} is empty: it is the only always-on file on this framework, "
            "so an empty one is a harness that does not exist"
        )
    skills = _check_skills(surface, tree, body, errors)
    subs = _check_subagents(surface, tree, allow_subagents, errors)
    for rel, source in tree.items():
        is_script = surface.script_pattern.fullmatch(rel)
        is_skill_script = surface.compile_skill_scripts and (
            surface.skill_script_pattern.fullmatch(rel) and rel.endswith(".py")
        )
        if is_script or is_skill_script:
            _py_compiles(rel, source, errors)
    if surface.hooks_file is not None and surface.hooks_file in tree:
        check_hooks(surface, tree[surface.hooks_file], errors)
    _check_script_refs(surface, tree, reserved_scripts, errors)

    if errors:
        raise TreeError(" | ".join(errors))
    return Census(
        always_on_chars=len(body),
        n_files=len(tree),
        skills=len(skills),
        sub_agents=len(subs),
        scripts=sum(1 for rel in tree if surface.script_pattern.fullmatch(rel)),
        plugins=sum(
            1 for rel in tree if surface.plugin_pattern and surface.plugin_pattern.fullmatch(rel)
        ),
        hooks=1 if surface.hooks_file is not None and surface.hooks_file in tree else 0,
    )


def diff_trees(surface: SurfaceSpec, before: Tree, after: Tree) -> Diff:
    return Diff(
        added=sorted(set(after) - set(before)),
        removed=sorted(set(before) - set(after)),
        modified=sorted(rel for rel in set(before) & set(after) if before[rel] != after[rel]),
        always_on_char_delta=len(always_on_text(surface, after))
        - len(always_on_text(surface, before)),
    )


def restricted_changes(surface: SurfaceSpec, before: Tree, after: Tree) -> list[str]:
    """Changed paths that execute code or loosen permissions and so always need approval."""
    return [rel for rel in diff_trees(surface, before, after).paths if surface.is_restricted(rel)]


def diversity(trees: dict[str, Tree]) -> tuple[int, float]:
    """(distinct trees, mean pairwise line-Jaccard): a collapse to one body shows as 1.0."""
    names = sorted(trees)
    if len(names) < 2:
        return len(names), 1.0

    def lines(tree: Tree) -> set[str]:
        return {
            f"{rel}:{ln}" for rel, text in tree.items() for ln in text.splitlines() if ln.strip()
        }

    sets = {name: lines(trees[name]) for name in names}
    sims: list[float] = []
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            union = sets[first] | sets[second]
            sims.append(len(sets[first] & sets[second]) / len(union) if union else 1.0)
    distinct = len({tuple(sorted(tree.items())) for tree in trees.values()})
    return distinct, round(sum(sims) / len(sims), 4)
