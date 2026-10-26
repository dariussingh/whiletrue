"""The outcome of one item's recursion, kept on disk until a person adopts or discards it.

`proposal.json` holds the harness the item started with, the harness the recursion chose, a diff,
the paths that execute code (and so always need approval), and the lesson to store. The adoption
steps (`rsi.adopt`, `rsi.discard`) and the `whiletrue rsi` commands read it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from whiletrue.rsi.harness2 import context
from whiletrue.rsi.harness2.library import Experience
from whiletrue.rsi.harness2.tree import Tree

Status = Literal["pending", "adopted", "discarded", "recorded"]


@dataclass
class Proposal:
    item_id: str
    domain: str
    surface: str
    base: Tree
    chosen: Tree
    chosen_label: str
    why: str = ""
    adopt: str = "approve"
    #: Changed paths that execute code or loosen permissions: always approved by a person.
    restricted: list[str] = field(default_factory=lambda: [])
    diff: str = ""
    experience: dict[str, str] = field(default_factory=lambda: {})
    ledger: dict[str, Any] = field(default_factory=lambda: {})
    run_id: str = ""
    #: Absolute path of the experience library the lesson goes to on adoption.
    library: str = ""
    status: Status = "pending"
    #: With `adopt: model`: the reviewing model's decision ("approve" or "reject") and reason.
    model_decision: str = ""
    model_why: str = ""

    @property
    def changed(self) -> bool:
        return self.base != self.chosen

    def lesson(self) -> Experience:
        return Experience(**self.experience)


def root(project_dir: Path) -> Path:
    return project_dir / ".whiletrue" / "rsi"


def item_dir(project_dir: Path, item_id: str) -> Path:
    return root(project_dir) / "items" / item_id.replace("/", "__")


def path_of(project_dir: Path, item_id: str) -> Path:
    return item_dir(project_dir, item_id) / "proposal.json"


def make(
    *,
    item_id: str,
    domain: str,
    surface_name: str,
    base: Tree,
    chosen: Tree,
    chosen_label: str,
    restricted: list[str],
    experience: Experience,
    why: str,
    adopt: str,
    ledger: dict[str, Any],
    run_id: str,
    library: str = "",
) -> Proposal:
    return Proposal(
        item_id=item_id,
        domain=domain,
        surface=surface_name,
        base=base,
        chosen=chosen,
        chosen_label=chosen_label,
        why=why,
        adopt=adopt,
        restricted=restricted,
        diff=context.unified_diff(base, chosen, chosen_label),
        experience={k: v for k, v in asdict(experience).items() if v},
        ledger=ledger,
        run_id=run_id,
        library=library,
        status="recorded" if adopt == "never" else "pending",
    )


def save(project_dir: Path, proposal: Proposal) -> Path:
    path = path_of(project_dir, proposal.item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(proposal), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load(project_dir: Path, item_id: str) -> Proposal | None:
    try:
        raw = cast("dict[str, Any]", json.loads(path_of(project_dir, item_id).read_text("utf-8")))
        return Proposal(**raw)
    except (OSError, ValueError, TypeError):
        return None


def all_items(project_dir: Path) -> list[Proposal]:
    folder = root(project_dir) / "items"
    if not folder.is_dir():
        return []
    found = [load(project_dir, p.name.replace("__", "/")) for p in sorted(folder.iterdir())]
    return [p for p in found if p is not None]


def set_status(project_dir: Path, proposal: Proposal, status: Status) -> None:
    proposal.status = status
    save(project_dir, proposal)
