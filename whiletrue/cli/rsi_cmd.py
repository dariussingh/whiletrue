"""`whiletrue rsi`: look at, adopt and reset what recursive self-improvement proposed."""

from __future__ import annotations

import shutil
from pathlib import Path

from whiletrue.cli.common import EXIT, Io
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.config import Harness2Config
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.surfaces import SURFACES

USAGE = """Usage: whiletrue rsi <command>

  status                Every item's proposal: state, chosen candidate, spend
  diff <item>           The harness change an item proposed
  library               The experience library, by domain
  adopt <item> [--yes]  Write a proposed harness into this project and store its lesson
                        (--yes is required when it changes files that execute code)
  reset <item>          Forget an item's rollouts and proposal so it starts fresh
"""


def run(io: Io, sub: str | None, item: str | None, *, yes: bool = False) -> int:
    project = Path(io.cwd)
    match sub:
        case "status":
            return _status(io, project)
        case "diff":
            return _diff(io, project, item)
        case "library":
            return _library(io, project)
        case "adopt":
            return _adopt(io, project, item, yes)
        case "reset":
            return _reset(io, project, item)
        case _:
            io.err(USAGE)
            return EXIT.usage


def _status(io: Io, project: Path) -> int:
    found = proposals.all_items(project)
    if not found:
        io.out("No rsi proposals yet.\n")
        return EXIT.ok
    for p in found:
        changed = "changed" if p.changed else "unchanged"
        spent = p.ledger.get("spent", 0.0)
        restricted = f"  needs approval: {', '.join(p.restricted)}" if p.restricted else ""
        model = f"  model: {p.model_decision}" if p.model_decision else ""
        io.out(
            f"{p.item_id}  {p.status}  {p.chosen_label}  {changed}  "
            f"{p.ledger.get('rollouts', 0)} rollouts, {p.ledger.get('sessions', 0)} sessions, "
            f"${float(spent):.2f}{model}{restricted}\n"
        )
    return EXIT.ok


def _need(io: Io, project: Path, item: str | None) -> proposals.Proposal | None:
    if item is None:
        io.err(USAGE)
        return None
    proposal = proposals.load(project, item)
    if proposal is None:
        io.err(f'No rsi proposal for "{item}"\n')
    return proposal


def _diff(io: Io, project: Path, item: str | None) -> int:
    proposal = _need(io, project, item)
    if proposal is None:
        return EXIT.usage if item is None else EXIT.failed
    if not proposal.changed:
        io.out(f"{proposal.item_id}: the chosen harness is the same as the current one\n")
        return EXIT.ok
    if proposal.restricted:
        io.out(f"# Changes files that execute code: {', '.join(proposal.restricted)}\n")
    io.out(proposal.diff)
    return EXIT.ok


def _library(io: Io, project: Path) -> int:
    root = project / Harness2Config().library
    if not root.is_dir() or not any(root.iterdir()):
        io.out("The experience library is empty.\n")
        return EXIT.ok
    for domain in sorted(p for p in root.iterdir() if p.is_dir()):
        names = (
            sorted(p.name for p in (domain / "harnesses").iterdir())
            if (domain / "harnesses").is_dir()
            else []
        )
        io.out(f"{domain.name}  ({len(names)} entries)\n")
        for name in names:
            io.out(f"  {name}\n")
    return EXIT.ok


def _adopt(io: Io, project: Path, item: str | None, yes: bool) -> int:
    proposal = _need(io, project, item)
    if proposal is None:
        return EXIT.usage if item is None else EXIT.failed
    if proposal.status not in ("pending", "recorded"):
        io.err(f'"{proposal.item_id}" is already {proposal.status}\n')
        return EXIT.failed
    if proposal.restricted and not yes:
        io.err(
            f"This harness changes files that execute code: {', '.join(proposal.restricted)}.\n"
            f"Read `whiletrue rsi diff {proposal.item_id}`, then repeat with --yes to adopt it.\n"
        )
        return EXIT.failed
    surface = SURFACES[proposal.surface]
    if proposal.changed:
        harness_tree.write_tree(surface, proposal.chosen, project, stubs=False)
    lesson = proposal.lesson()
    if lesson.complete and proposal.library:
        Library(Path(proposal.library), proposal.domain, surface).add(
            proposal.item_id,
            proposal.chosen,
            lesson,
            why=proposal.why,
            label=proposal.chosen_label,
            run_id=proposal.run_id,
        )
    proposals.set_status(project, proposal, "adopted")
    io.out(f"Adopted the harness for {proposal.item_id} into {project}\n")
    return EXIT.ok


def _reset(io: Io, project: Path, item: str | None) -> int:
    if item is None:
        io.err(USAGE)
        return EXIT.usage
    folder = proposals.item_dir(project, item)
    if not folder.is_dir():
        io.err(f'Nothing to reset for "{item}"\n')
        return EXIT.failed
    shutil.rmtree(folder)
    io.out(f"Forgot the rsi rollouts and proposal for {item}\n")
    return EXIT.ok
