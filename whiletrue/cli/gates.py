"""Commands for the humans in the loop: approve, reject, comment, history."""

from __future__ import annotations

import getpass
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from whiletrue.cli.common import EXIT, Io
from whiletrue.core import Decision
from whiletrue.store_sqlite import SqliteStore


def _now() -> int:
    return int(time.time() * 1000)


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover (no passwd entry)
        return "cli"


def open_store(io: Io) -> SqliteStore | None:
    db_path = Path(io.cwd) / ".whiletrue" / "state.db"
    if not db_path.exists():
        io.err("No runs yet (.whiletrue/state.db not found)\n")
        return None
    return SqliteStore(db_path)


def decide(io: Io, decision: Decision, item: str | None, note: str | None, step: str | None) -> int:
    verb = "approve" if decision == "approved" else "reject"
    if item is None:
        io.err(
            f"Usage: whiletrue {verb} <item>{' --note "..."' if decision == 'rejected' else ''}\n"
        )
        return EXIT.usage
    if decision == "rejected" and not (note and note.strip()):
        io.err('A rejection needs a reason: whiletrue reject <item> --note "what to change"\n')
        return EXIT.usage
    store = open_store(io)
    if store is None:
        return EXIT.failed
    try:
        waiting = store.list_parks(item, undecided_only=True)
        if step is not None:
            waiting = [p for p in waiting if p.node_path == step]
        if not waiting:
            io.err(f'Nothing is waiting on a decision for "{item}"\n')
            return EXIT.failed
        steps = sorted({p.node_path for p in waiting})
        if len(steps) > 1:
            io.err(f'"{item}" is waiting at several gates ({", ".join(steps)}); pass --step <id>\n')
            return EXIT.usage
        park = waiting[-1]
        store.decide_park(park.id, decision, decided_by=_who(), decided_at=_now(), notes=note)
        io.out(f"{item}: {decision} ({park.what} at {park.node_path})\n")
        io.out("Run `whiletrue resume` to continue (a watching run picks it up by itself).\n")
        return EXIT.ok
    finally:
        store.close()


def comment(io: Io, item: str | None, text: str | None) -> int:
    if item is None or not text or not text.strip():
        io.err('Usage: whiletrue comment <item> "your comment"\n')
        return EXIT.usage
    store = open_store(io)
    if store is None:
        return EXIT.failed
    try:
        added = store.add_comment(
            item_id=item,
            source="local",
            external_id=uuid.uuid4().hex,
            author=_who(),
            body=text.strip(),
            created_at=_now(),
        )
        assert added is not None
        io.out(f"comment #{added.id} added to {item}; the pipeline answers it at the next wake\n")
        return EXIT.ok
    finally:
        store.close()


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S")


def history(io: Io, item: str | None) -> int:
    if item is None:
        io.err("Usage: whiletrue history <item>\n")
        return EXIT.usage
    store = open_store(io)
    if store is None:
        return EXIT.failed
    try:
        lines: list[tuple[int, str]] = []
        for park in store.list_parks(item):
            lines.append((park.created_at, f"parked at {park.node_path}: waiting for {park.what}"))
            if park.decision is not None and park.decided_at is not None:
                note = f" — {park.notes}" if park.notes else ""
                lines.append(
                    (park.decided_at, f"{park.decision} by {park.decided_by}{note}"),
                )
        for c in store.list_comments(item):
            lines.append((c.created_at, f"comment from {c.author} ({c.source}): {c.body}"))
            if c.reply:
                lines.append((c.created_at, f"  reply: {c.reply}"))
        for s in store.list_steers(item):
            lines.append((s.created_at, f"steered {s.step}: {s.prompt}"))
        if not lines:
            io.err(f'No history for "{item}"\n')
            return EXIT.failed
        for when, text in sorted(lines, key=lambda x: x[0]):
            io.out(f"{_stamp(when)}  {text}\n")
        return EXIT.ok
    finally:
        store.close()


def learnings(io: Io, action: str | None, learning_id: str | None) -> int:
    """List proposed lessons, or approve/reject one. Only approved lessons reach agents."""
    store = open_store(io)
    if store is None:
        return EXIT.failed
    try:
        if action in (None, "list"):
            rows = store.list_learnings()
            if not rows:
                io.out("No learnings yet\n")
                return EXIT.ok
            for row in rows:
                source = row.provenance.get("source", "?")
                item = row.provenance.get("item", "?")
                io.out(f"#{row.id}  {row.status:<8}  {row.note}  (from {source} on {item})\n")
            return EXIT.ok
        if action not in ("approve", "reject") or not (learning_id or "").isdigit():
            io.err("Usage: whiletrue learnings [list | approve <id> | reject <id>]\n")
            return EXIT.usage
        try:
            store.set_learning_status(
                int(learning_id or 0), "approved" if action == "approve" else "rejected"
            )
        except KeyError:
            io.err(f"No learning #{learning_id}\n")
            return EXIT.failed
        io.out(f"learning #{learning_id}: {action}d\n")
        return EXIT.ok
    finally:
        store.close()
